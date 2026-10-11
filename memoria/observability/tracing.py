from __future__ import annotations

import time
import re
from typing import Any

from ..store import Store
from .timing import StageTimings


class TurnTracer:
    def __init__(self, store: Store, session_id: str):
        self.store, self.session_id = store, session_id
        self.started = time.perf_counter()
        self.timings = StageTimings()
        self.llm_started: float | None = None
        self.first_text: float | None = None

    def mark_llm_request(self):
        if self.llm_started is None:
            self.llm_started = time.perf_counter()

    def mark_visible_text(self, text: str):
        if text and self.first_text is None:
            self.first_text = time.perf_counter()

    def finish(self, status: str, steps: int, memories: list[dict], tools: list[dict], error: str | None = None, metadata: dict[str, Any] | None = None) -> dict:
        metadata = {**(metadata or {}), "stage_timings": self.timings.public_dict(),
            "ttft": {"turn_ms": round((self.first_text - self.started) * 1000, 3) if self.first_text is not None else None,
                     "llm_ms": round((self.first_text - self.llm_started) * 1000, 3) if self.first_text is not None and self.llm_started is not None else None,
                     "scope": "first non-empty visible streamed text; reasoning/tool deltas excluded; non-streaming is null"}}
        safe_memories = [{key:item.get(key) for key in ("id","kind","importance","source") if key in item} for item in memories]
        safe_tools = _sanitize(tools)
        return self.store.add_trace(self.session_id, status, steps, int((time.perf_counter()-self.started)*1000), safe_memories, safe_tools, _redact_text(error) if error else None, _sanitize(metadata or {}))


_SENSITIVE_KEYS = {"api_key", "apikey", "authorization", "password", "secret", "token", "access_token", "client_secret"}


def _sanitize(value: Any, key: str = "") -> Any:
    if key.lower() in _SENSITIVE_KEYS: return "[REDACTED]"
    if isinstance(value, dict): return {str(k):_sanitize(v, str(k)) for k,v in value.items()}
    if isinstance(value, list): return [_sanitize(item) for item in value]
    if isinstance(value, tuple): return [_sanitize(item) for item in value]
    if isinstance(value, str): return _redact_text(value)
    return value


def _redact_text(value: str) -> str:
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
    return re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "sk-[REDACTED]", text)
