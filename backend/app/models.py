from dataclasses import asdict, dataclass
from typing import Any, Literal


Decision = Literal["approved", "denied", "escalated", "needs_info"]


@dataclass
class Serializable:
    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChatRequest(Serializable):
    message: str
    session_id: str | None = None


@dataclass
class ToolCall(Serializable):
    name: str
    input: dict[str, Any]
    output: dict[str, Any]
    latency_ms: int
    status: Literal["success", "failed", "retried"]


@dataclass
class ReasoningStep(Serializable):
    title: str
    detail: str
    status: Literal["ok", "warning", "failed"]


@dataclass
class AgentTrace(Serializable):
    trace_id: str
    session_id: str
    started_at: str
    latency_ms: int
    estimated_prompt_tokens: int
    estimated_completion_tokens: int
    estimated_cost_usd: float
    llm_provider: str
    llm_model: str
    retries: int
    decision: Decision
    tool_calls: list[ToolCall]
    reasoning: list[ReasoningStep]


@dataclass
class ChatResponse(Serializable):
    reply: str
    decision: Decision
    trace: AgentTrace


@dataclass
class TraceSummary(Serializable):
    trace_id: str
    session_id: str
    started_at: str
    latency_ms: int
    estimated_prompt_tokens: int
    estimated_completion_tokens: int
    retries: int
    decision: str
    estimated_cost_usd: float
    llm_provider: str
    llm_model: str
    tool_calls: list[ToolCall]
    reasoning: list[ReasoningStep]
