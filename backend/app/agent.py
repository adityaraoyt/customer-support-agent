from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from typing import Any, TypedDict
from uuid import uuid4

try:
    from langgraph.graph import END, StateGraph
except ImportError:  # pragma: no cover - keeps the stdlib demo usable before dependencies are installed.
    END = "__end__"
    StateGraph = None  # type: ignore[assignment]

from .crm import find_customer, find_order
from .data_loader import load_customers, load_policy
from .models import AgentTrace, ChatRequest, ChatResponse, ReasoningStep, ToolCall, TraceSummary
from .openrouter_client import OpenRouterClient
from .policy import evaluate_refund_policy


TODAY = datetime(2026, 6, 9, tzinfo=UTC).date()
TOKEN_PRICE_PER_1K = 0.0006


class AgentState(TypedDict, total=False):
    request: ChatRequest
    session_id: str
    extraction: dict[str, str | None]
    customer: dict[str, Any] | None
    order: dict[str, Any] | None
    policy_result: dict[str, Any]
    decision: str
    reply: str
    tool_calls: list[ToolCall]
    reasoning: list[ReasoningStep]
    retries: int
    prompt_tokens: int
    completion_tokens: int
    llm_provider: str
    llm_model: str


class RefundAgent:
    def __init__(self) -> None:
        self.customers = load_customers()
        self.policy = load_policy()
        self.llm = OpenRouterClient()
        self.traces: list[TraceSummary] = []
        self.sessions: dict[str, list[dict[str, str]]] = {}
        self.graph = self._build_graph()

    def handle(self, request: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        trace_id = f"trc_{uuid4().hex[:10]}"
        session_id = request.session_id or f"ses_{uuid4().hex[:8]}"
        self.sessions.setdefault(session_id, []).append({"role": "customer", "content": request.message})

        state: AgentState = {
            "request": request,
            "session_id": session_id,
            "tool_calls": [],
            "reasoning": [],
            "retries": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "llm_provider": "openrouter",
            "llm_model": self.llm.model,
        }
        final_state = self.graph.invoke(state) if self.graph else self._run_without_langgraph(state)
        self.sessions[session_id].append({"role": "agent", "content": final_state["reply"]})
        self.sessions[session_id] = self.sessions[session_id][-12:]

        return self._finish(trace_id, started, final_state)

    def get_traces(self) -> list[TraceSummary]:
        return list(reversed(self.traces[-25:]))

    def _build_graph(self) -> Any:
        if StateGraph is None:
            return None

        graph = StateGraph(AgentState)
        graph.add_node("safety", self._safety_node)
        graph.add_node("understand_intent", self._understand_intent_node)
        graph.add_node("lookup_customer", self._lookup_customer_node)
        graph.add_node("validate_order", self._validate_order_node)
        graph.add_node("validate_policy", self._validate_policy_node)
        graph.add_node("respond", self._respond_node)
        graph.set_entry_point("safety")
        graph.add_edge("safety", "understand_intent")
        graph.add_edge("understand_intent", "lookup_customer")
        graph.add_conditional_edges(
            "lookup_customer",
            lambda state: "validate_order" if state.get("customer") else "respond",
            {"validate_order": "validate_order", "respond": "respond"},
        )
        graph.add_conditional_edges(
            "validate_order",
            lambda state: "validate_policy" if state.get("order") else "respond",
            {"validate_policy": "validate_policy", "respond": "respond"},
        )
        graph.add_edge("validate_policy", "respond")
        graph.add_edge("respond", END)
        return graph.compile()

    def _run_without_langgraph(self, state: AgentState) -> AgentState:
        for node in (self._safety_node, self._understand_intent_node, self._lookup_customer_node):
            state = node(state)
        if state.get("customer"):
            state = self._validate_order_node(state)
        if state.get("order"):
            state = self._validate_policy_node(state)
        return self._respond_node(state)

    def _safety_node(self, state: AgentState) -> AgentState:
        state["reasoning"].append(
            ReasoningStep(
                title="Prompt-safety check",
                detail="Customer text is untrusted input. The CRM database and deterministic refund policy remain authoritative.",
                status="ok",
            )
        )
        return state

    def _understand_intent_node(self, state: AgentState) -> AgentState:
        request = state["request"]
        system = (
            "Extract refund-support intent from the user message. Return only JSON with keys: "
            "intent, customer_name, email, order_id, missing_fields. Do not decide refund eligibility."
        )
        user = json.dumps(
            {
                "message": request.message,
                "session_history": self.sessions.get(state["session_id"], [])[-6:],
                "known_customer_names": [customer["name"] for customer in self.customers],
            }
        )
        llm_result = self.llm.chat_json(system, user)
        state["prompt_tokens"] += llm_result.prompt_tokens
        state["completion_tokens"] += llm_result.completion_tokens
        state["llm_model"] = llm_result.model

        extraction = self._parse_llm_extraction(llm_result.content)
        fallback_extraction, fallback_status = self._extract_entities_deterministically(request.message)
        extraction = {key: extraction.get(key) or fallback_extraction.get(key) for key in fallback_extraction}
        status = "success" if llm_result.used_provider and fallback_status == "success" else "retried"
        if not llm_result.used_provider or fallback_status == "retried":
            state["retries"] += 1

        output = {
            "provider_used": llm_result.used_provider,
            "model": llm_result.model,
            "extraction": extraction,
            "provider_error": llm_result.error,
        }
        state["extraction"] = extraction
        state["tool_calls"].append(
            ToolCall(
                name="llm.openrouter_extract_intent",
                input={"message": request.message},
                output=output,
                latency_ms=llm_result.latency_ms,
                status=status,  # type: ignore[arg-type]
            )
        )
        if llm_result.error:
            state["reasoning"].append(
                ReasoningStep(
                    title="LLM fallback",
                    detail=f"{llm_result.error} Deterministic entity extraction was used for continuity.",
                    status="warning",
                )
            )
        else:
            state["reasoning"].append(
                ReasoningStep(
                    title="Intent understood",
                    detail="OpenRouter classified the request and extracted candidate customer/order fields.",
                    status="ok",
                )
            )
        return state

    def _lookup_customer_node(self, state: AgentState) -> AgentState:
        started = time.perf_counter()
        customer = find_customer(self.customers, state["extraction"])
        state["customer"] = customer
        state["tool_calls"].append(
            self._tool_call(
                "crm.lookup_customer",
                state["extraction"],
                {"found": bool(customer), "customer": customer},
                started,
                "success" if customer else "failed",
            )
        )
        if not customer:
            state["decision"] = "needs_info"
        return state

    def _validate_order_node(self, state: AgentState) -> AgentState:
        started = time.perf_counter()
        customer = state["customer"]
        order = find_order(customer, state["extraction"]) if customer else None
        state["order"] = order
        state["tool_calls"].append(
            self._tool_call(
                "crm.validate_order_ownership",
                {"customer_id": customer["customer_id"] if customer else None, "order_id": state["extraction"].get("order_id")},
                {"found": bool(order), "order": order},
                started,
                "success" if order else "failed",
            )
        )
        if not order:
            state["decision"] = "needs_info"
        return state

    def _validate_policy_node(self, state: AgentState) -> AgentState:
        started = time.perf_counter()
        result = evaluate_refund_policy(state["order"], state["request"].message, TODAY)
        state["policy_result"] = result
        state["decision"] = result["decision"]
        state["reasoning"].extend(result["reasoning"])
        state["tool_calls"].append(
            self._tool_call(
                "policy.validate_refund",
                {"order": state["order"], "today": TODAY.isoformat(), "policy_excerpt": "refund_policy.md"},
                {"decision": result["decision"], "reason": result["reason"]},
                started,
            )
        )
        return state

    def _respond_node(self, state: AgentState) -> AgentState:
        customer = state.get("customer")
        order = state.get("order")
        if not customer:
            state["reply"] = (
                "I can help with that, but I need the customer name or email tied to the order before I can evaluate a refund."
            )
            state["decision"] = "needs_info"
            return state
        if not order:
            state["reply"] = f"I found {customer['name']}, but I need a valid order number from their account to continue."
            state["decision"] = "needs_info"
            return state

        result = state["policy_result"]
        decision = result["decision"]
        if decision == "approved":
            state["reply"] = (
                f"Thanks, {customer['name']}. I approved the refund for {order['order_id']} "
                f"for ${order['total']:.2f}. It should return to the original payment method within 5-7 business days."
            )
        elif decision == "escalated":
            state["reply"] = (
                f"Thanks, {customer['name']}. I cannot approve {order['order_id']} automatically because {result['reason']} "
                "I escalated it to a human support specialist."
            )
        else:
            state["reply"] = (
                f"I understand this is frustrating, {customer['name']}, but I cannot refund {order['order_id']}. "
                f"The policy reason is: {result['reason']}"
            )
        return state

    def _parse_llm_extraction(self, content: str) -> dict[str, str | None]:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = {}
        order_id = parsed.get("order_id")
        if isinstance(order_id, str):
            order_id = order_id.upper()
        return {
            "customer_name": parsed.get("customer_name") if isinstance(parsed.get("customer_name"), str) else None,
            "email": parsed.get("email") if isinstance(parsed.get("email"), str) else None,
            "order_id": order_id if isinstance(order_id, str) else None,
        }

    def _extract_entities_deterministically(self, message: str) -> tuple[dict[str, str | None], str]:
        strict_order = re.search(r"\bORD-\d{4}\b", message, re.IGNORECASE)
        email = re.search(r"[\w.+-]+@[\w.-]+\.\w+", message)
        name = None
        lowered = message.lower()
        for customer in self.customers:
            if customer["name"].lower() in lowered:
                name = customer["name"]
                break

        status = "success"
        order_id = strict_order.group(0).upper() if strict_order else None
        if not order_id:
            loose_order = re.search(r"\b(?:order|ord)\s*#?\s*(\d{4})\b", message, re.IGNORECASE)
            if loose_order:
                order_id = f"ORD-{loose_order.group(1)}"
                status = "retried"

        return {"customer_name": name, "email": email.group(0) if email else None, "order_id": order_id}, status

    def _finish(self, trace_id: str, started: float, state: AgentState) -> ChatResponse:
        request = state["request"]
        reply = state["reply"]
        prompt_tokens = state["prompt_tokens"] #or max(1, len((request.message + self.policy).split()))
        completion_tokens = state["completion_tokens"] 
        #or max(
         #   1, len(reply.split()) + sum(len(step.detail.split()) for step in state["reasoning"])
        #)
        trace = AgentTrace(
            trace_id=trace_id,
            session_id=state["session_id"],
            started_at=datetime.now(UTC).isoformat(),
            latency_ms=int((time.perf_counter() - started) * 1000),
            estimated_prompt_tokens=prompt_tokens,
            estimated_completion_tokens=completion_tokens,
            estimated_cost_usd=round(((prompt_tokens + completion_tokens) / 1000) * TOKEN_PRICE_PER_1K, 6),
            llm_provider=state["llm_provider"],
            llm_model=state["llm_model"],
            retries=state["retries"],
            decision=state["decision"],  # type: ignore[arg-type]
            tool_calls=state["tool_calls"],
            reasoning=state["reasoning"],
        )
        self.traces.append(TraceSummary(**trace.model_dump()))
        return ChatResponse(reply=reply, decision=trace.decision, trace=trace)

    def _tool_call(
        self,
        name: str,
        input_data: dict[str, Any],
        output: dict[str, Any],
        started: float,
        status: str = "success",
    ) -> ToolCall:
        return ToolCall(
            name=name,
            input=input_data,
            output=output,
            latency_ms=int((time.perf_counter() - started) * 1000),
            status=status,  # type: ignore[arg-type]
        )
