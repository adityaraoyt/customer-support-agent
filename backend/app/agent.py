from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .data_loader import load_customers, load_policy
from .models import AgentTrace, ChatRequest, ChatResponse, ReasoningStep, ToolCall, TraceSummary


TODAY = datetime(2026, 6, 9, tzinfo=UTC).date()
TOKEN_PRICE_PER_1K = 0.0006


class RefundAgent:
    def __init__(self) -> None:
        self.customers = load_customers()
        self.policy = load_policy()
        self.traces: list[TraceSummary] = []

    def handle(self, request: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        trace_id = f"trc_{uuid4().hex[:10]}"
        session_id = request.session_id or f"ses_{uuid4().hex[:8]}"
        tool_calls: list[ToolCall] = []
        reasoning: list[ReasoningStep] = []
        retries = 0

        reasoning.append(
            ReasoningStep(
                title="Prompt-safety check",
                detail="Customer message is treated as untrusted input. Refund policy and CRM tools remain the source of truth.",
                status="ok",
            )
        )

        extraction, extraction_call = self._extract_entities(request.message)
        tool_calls.append(extraction_call)
        if extraction_call.status == "retried":
            retries += 1
            reasoning.append(
                ReasoningStep(
                    title="Retry",
                    detail="Initial entity extraction was incomplete, so the agent retried with looser matching.",
                    status="warning",
                )
            )

        customer, lookup_call = self._find_customer(extraction)
        tool_calls.append(lookup_call)
        if not customer:
            return self._finish(
                request,
                trace_id,
                session_id,
                started,
                retries,
                "needs_info",
                "I can help with that, but I need the customer name or email tied to the order before I can evaluate a refund.",
                tool_calls,
                reasoning,
            )

        order, order_call = self._find_order(customer, extraction)
        tool_calls.append(order_call)
        if not order:
            return self._finish(
                request,
                trace_id,
                session_id,
                started,
                retries,
                "needs_info",
                f"I found {customer['name']}, but I need a valid order number from their account to continue.",
                tool_calls,
                reasoning,
            )

        policy_result, policy_call = self._evaluate_policy(order, request.message)
        tool_calls.append(policy_call)
        decision = policy_result["decision"]
        reasoning.extend(policy_result["reasoning"])
        reply = self._compose_reply(customer, order, policy_result)

        return self._finish(
            request,
            trace_id,
            session_id,
            started,
            retries,
            decision,
            reply,
            tool_calls,
            reasoning,
        )

    def get_traces(self) -> list[TraceSummary]:
        return list(reversed(self.traces[-25:]))

    def _extract_entities(self, message: str) -> tuple[dict[str, str | None], ToolCall]:
        started = time.perf_counter()
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

        output = {"customer_name": name, "email": email.group(0) if email else None, "order_id": order_id}
        return output, self._tool_call("extract_refund_entities", {"message": message}, output, started, status)

    def _find_customer(self, extraction: dict[str, str | None]) -> tuple[dict[str, Any] | None, ToolCall]:
        started = time.perf_counter()
        name = extraction.get("customer_name")
        email = extraction.get("email")
        for customer in self.customers:
            if email and customer["email"].lower() == email.lower():
                return customer, self._tool_call("crm.lookup_customer", extraction, {"found": True, "customer": customer}, started)
            if name and customer["name"].lower() == name.lower():
                return customer, self._tool_call("crm.lookup_customer", extraction, {"found": True, "customer": customer}, started)
        return None, self._tool_call("crm.lookup_customer", extraction, {"found": False}, started, "failed")

    def _find_order(
        self, customer: dict[str, Any], extraction: dict[str, str | None]
    ) -> tuple[dict[str, Any] | None, ToolCall]:
        started = time.perf_counter()
        order_id = extraction.get("order_id")
        for order in customer["orders"]:
            if order["order_id"].lower() == (order_id or "").lower():
                return order, self._tool_call(
                    "crm.lookup_order",
                    {"customer_id": customer["customer_id"], "order_id": order_id},
                    {"found": True, "order": order},
                    started,
                )
        return None, self._tool_call(
            "crm.lookup_order",
            {"customer_id": customer["customer_id"], "order_id": order_id},
            {"found": False},
            started,
            "failed",
        )

    def _evaluate_policy(self, order: dict[str, Any], message: str) -> tuple[dict[str, Any], ToolCall]:
        started = time.perf_counter()
        reasoning: list[ReasoningStep] = []
        total = float(order["total"])
        days_since_delivery = (TODAY - datetime.fromisoformat(order["delivered_at"]).date()).days
        message_lower = message.lower()

        if any(
            term in message_lower
            for term in ["ignore previous", "ignore all previous", "override", "break the rules", "manager told you"]
        ):
            reasoning.append(
                ReasoningStep(
                    title="Prompt injection ignored",
                    detail="The customer attempted to override policy instructions. The agent continued using the written policy.",
                    status="warning",
                )
            )

        if order["status"] != "delivered":
            result = {
                "decision": "denied",
                "reason": "Only delivered orders are eligible for refund review.",
                "reasoning": reasoning,
            }
        elif order.get("final_sale"):
            result = {
                "decision": "denied",
                "reason": "Final sale items cannot be refunded.",
                "reasoning": reasoning,
            }
        elif days_since_delivery > 30:
            result = {
                "decision": "denied",
                "reason": f"The request is outside the 30-day refund window ({days_since_delivery} days since delivery).",
                "reasoning": reasoning,
            }
        elif order.get("opened") and not order.get("defective"):
            result = {
                "decision": "denied",
                "reason": "Opened non-defective electronics, beauty, and personal-use items are not refundable.",
                "reasoning": reasoning,
            }
        elif total > 500:
            result = {
                "decision": "escalated",
                "reason": "Refunds over $500 require human review.",
                "reasoning": reasoning,
            }
        elif order.get("chargeback_open"):
            result = {
                "decision": "escalated",
                "reason": "Orders with an open chargeback must be escalated.",
                "reasoning": reasoning,
            }
        else:
            result = {
                "decision": "approved",
                "reason": "The order is delivered, within 30 days, not final sale, and below the escalation threshold.",
                "reasoning": reasoning,
            }

        result["reasoning"].append(
            ReasoningStep(
                title="Policy evaluation",
                detail=result["reason"],
                status="ok" if result["decision"] == "approved" else "warning",
            )
        )
        return result, self._tool_call(
            "policy.evaluate_refund",
            {"order": order, "today": TODAY.isoformat(), "policy_excerpt": "refund_policy.md"},
            {"decision": result["decision"], "reason": result["reason"]},
            started,
        )

    def _compose_reply(self, customer: dict[str, Any], order: dict[str, Any], result: dict[str, Any]) -> str:
        decision = result["decision"]
        if decision == "approved":
            return (
                f"Thanks, {customer['name']}. I approved the refund for {order['order_id']} "
                f"for ${order['total']:.2f}. It should return to the original payment method within 5-7 business days."
            )
        if decision == "escalated":
            return (
                f"Thanks, {customer['name']}. I cannot approve {order['order_id']} automatically because {result['reason']} "
                "I escalated it to a human support specialist."
            )
        return (
            f"I understand this is frustrating, {customer['name']}, but I cannot refund {order['order_id']}. "
            f"The policy reason is: {result['reason']}"
        )

    def _finish(
        self,
        request: ChatRequest,
        trace_id: str,
        session_id: str,
        started: float,
        retries: int,
        decision: str,
        reply: str,
        tool_calls: list[ToolCall],
        reasoning: list[ReasoningStep],
    ) -> ChatResponse:
        prompt_tokens = max(1, len((request.message + self.policy).split()))
        completion_tokens = max(1, len(reply.split()) + sum(len(step.detail.split()) for step in reasoning))
        latency_ms = int((time.perf_counter() - started) * 1000)
        trace = AgentTrace(
            trace_id=trace_id,
            session_id=session_id,
            started_at=datetime.now(UTC).isoformat(),
            latency_ms=latency_ms,
            estimated_prompt_tokens=prompt_tokens,
            estimated_completion_tokens=completion_tokens,
            estimated_cost_usd=round(((prompt_tokens + completion_tokens) / 1000) * TOKEN_PRICE_PER_1K, 6),
            retries=retries,
            decision=decision,  # type: ignore[arg-type]
            tool_calls=tool_calls,
            reasoning=reasoning,
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
