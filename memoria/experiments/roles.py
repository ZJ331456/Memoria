from __future__ import annotations

import copy
import hashlib
import json
import time
import tomllib
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path

from ..llm import LLMClient
from ..observability.timing import StageTimings
from .records import safe_endpoint


@dataclass(frozen=True)
class RoleConfig:
    slot: str = "main"
    max_tokens: int = 512
    temperature: float = 0
    top_p: float = 1
    timeout_seconds: float = 120
    max_retries: int = 0
    thinking: str = "disabled_by_memoria_local_server"

    def __post_init__(self):
        if self.slot not in {"main", "fast"} or self.max_tokens < 1 or self.timeout_seconds <= 0 or self.max_retries < 0:
            raise ValueError("invalid experiment role configuration")
        if not 0 <= self.temperature <= 2 or not 0 < self.top_p <= 1:
            raise ValueError("invalid generation parameters")
        if self.thinking not in {"disabled_by_memoria_local_server", "provider_default_unknown", "disabled_by_deepseek_api"}:
            raise ValueError("unsupported thinking policy; do not assume arbitrary providers support it")


def load_roles(path: Path | None) -> dict[str, RoleConfig]:
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path else {}
    source = data.get("roles", {})
    if set(source) - {"reader", "extractor", "decider", "judge"}:
        raise ValueError("unknown experiment role")
    return {role: RoleConfig(**source.get(role, {})) for role in ("reader", "extractor", "decider", "judge")}


class ExperimentLLM(LLMClient):
    """Use production extraction/decision prompts with explicit role settings and actual call logs."""
    def __init__(self, settings, roles: dict[str, RoleConfig], recorder, timings: StageTimings):
        super().__init__(settings)
        self.roles, self.recorder, self.timings = roles, recorder, timings
        self.role = ContextVar("experiment_llm_role", default="reader")
        self.calls: list[dict] = []

    def public_roles(self):
        return {role: {**asdict(cfg), "model": getattr(self.settings, cfg.slot).model,
                       "endpoint": safe_endpoint(getattr(self.settings, cfg.slot).base_url)} for role, cfg in self.roles.items()}

    async def call(self, role: str, messages: list[dict], *, stream: bool = False):
        cfg = self.roles[role]
        settings = copy.copy(self.settings)
        settings.request_timeout_seconds, settings.max_retries = cfg.timeout_seconds, cfg.max_retries
        client = LLMClient(settings, self.transport)
        selected = getattr(settings, cfg.slot)
        selected = copy.copy(selected)
        if cfg.thinking == "disabled_by_deepseek_api":
            selected.thinking = "disabled"
        if cfg.thinking == "disabled_by_memoria_local_server" and not (selected.is_local_http() or selected.is_local_process()):
            raise ValueError("remote thinking policy must be explicitly marked provider_default_unknown")
        # The local policy is a deployment assertion; the DeepSeek-only policy is
        # validated by the official HTTP backend and emitted as an explicit field.
        prompt_sha = hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        call_id = len(self.calls)
        self.recorder.append("prompts.jsonl", {"call": call_id, "role": role, "sha256": prompt_sha, "messages": messages})
        started, first_text = time.perf_counter(), None

        async def delta(text):
            nonlocal first_text
            if text and first_text is None:
                first_text = time.perf_counter()

        row = {"call": call_id, "role": role, "prompt_sha256": prompt_sha,
               "model": selected.model, "generation": asdict(cfg), "usage": None,
               "ttft_ms": None, "error": None}
        try:
            with self.timings.measure(f"llm.{role}"):
                kwargs = dict(model=selected, max_tokens=cfg.max_tokens, temperature=cfg.temperature, top_p=cfg.top_p)
                result = await client.chat_stream(messages, on_delta=delta, **kwargs) if stream else await client.chat(messages, **kwargs)
            row.update(usage=result.usage or None, retries=result.retries, finish_reason=result.finish_reason,
                       response=result.content, tool_calls=result.tool_calls)
            if result.finish_reason not in {None, "stop"} or not result.content.strip():
                raise ValueError(f"incomplete final content: {result.finish_reason}")
            return result
        except BaseException as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            row["ttft_ms"] = round((first_text - started) * 1000, 3) if first_text is not None else None
            self.calls.append(row)
            self.recorder.append("calls.jsonl", row)

    async def complete(self, messages, model=None, max_tokens=None):
        return (await self.call(self.role.get(), messages)).content

    async def extract_memories(self, user_text, assistant_text):
        token = self.role.set("extractor")
        try:
            return await super().extract_memories(user_text, assistant_text)
        finally:
            self.role.reset(token)

    async def decide_memory_relation(self, content, kind, candidates):
        token = self.role.set("decider")
        try:
            return await super().decide_memory_relation(content, kind, candidates)
        finally:
            self.role.reset(token)
