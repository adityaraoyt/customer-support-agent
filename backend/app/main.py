import asyncio
import json
from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .models import ChatRequest, ChatResponse, TraceSummary
from .agent import RefundAgent


app = FastAPI(title="AI Customer Support Refund Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = RefundAgent()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    return agent.handle(request)


@app.get("/api/traces", response_model=list[TraceSummary])
def traces() -> list[TraceSummary]:
    return agent.get_traces()


@app.get("/api/traces/stream")
async def trace_stream() -> StreamingResponse:
    async def events():
        last_trace_id = None
        while True:
            traces = agent.get_traces()
            latest = traces[0] if traces else None
            if latest and latest.trace_id != last_trace_id:
                last_trace_id = latest.trace_id
                yield f"data: {json.dumps(to_jsonable(latest))}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream")


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    return value
