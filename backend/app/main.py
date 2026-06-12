from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
