from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from dotenv import load_dotenv

load_dotenv()


DEFAULT_MODEL = "openrouter/free"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


@dataclass
class LLMResult:
    content: str
    prompt_tokens: int
    completion_tokens: int
    model: str
    latency_ms: int
    used_provider: bool
    error: str | None = None


class OpenRouterClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        self.model = os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

    def chat_json(self, system: str, user: str) -> LLMResult:
        started = time.perf_counter()
        if not self.api_key:
            return LLMResult(
                content="{}",
                prompt_tokens=max(1, len((system + user).split())),
                completion_tokens=1,
                model=self.model,
                latency_ms=0,
                used_provider=False,
                error="OPENROUTER_API_KEY is not configured.",
            )

        payload = {
    "model": self.model,
    "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ],
    "response_format": {"type": "json_object"},
    "temperature": 0,
    "max_tokens": 1024,  # 👈 ADD THIS
}
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            OPENROUTER_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://127.0.0.1:8000",
                "X-Title": "Refund Agent Console",
            },
        )

        try:
            with urlopen(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return self._failed_result(system, user, started, f"OpenRouter HTTP {exc.code}: {detail[:240]}")
        except (TimeoutError, URLError) as exc:
            return self._failed_result(system, user, started, f"OpenRouter request failed: {exc}")

        message = data.get("choices", [{}])[0].get("message", {})
        usage: dict[str, Any] = data.get("usage") or {}
        return LLMResult(
            content=str(message.get("content") or "{}"),
            prompt_tokens=int(usage.get("prompt_tokens") or max(1, len((system + user).split()))),
            completion_tokens=int(usage.get("completion_tokens") or max(1, len(str(message.get("content") or "").split()))),
            model=str(data.get("model") or self.model),
            latency_ms=int((time.perf_counter() - started) * 1000),
            used_provider=True,
        )

    def _failed_result(self, system: str, user: str, started: float, error: str) -> LLMResult:
        return LLMResult(
            content="{}",
            prompt_tokens=max(1, len((system + user).split())),
            completion_tokens=1,
            model=self.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            used_provider=False,
            error=error,
        )
