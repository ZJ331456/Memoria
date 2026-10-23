"""Private all-stage request ledger and pre-send bounds for the two-arm evaluation."""
from __future__ import annotations

import hashlib
import time
import math
from datetime import datetime, timezone
from contextlib import contextmanager
from contextvars import ContextVar

from ..llm.client import LLMClient
from ..llm.deepseek_cache import canonical, cache_summary, layout_of
from .deepseek_cache import PRICING, cost_bounds

context = ContextVar("evaluation_cost_context", default={})


@contextmanager
def phase(arm, stage, case_id):
    token = context.set({"arm": arm, "stage": stage, "case_id": case_id})
    try:
        yield
    finally:
        context.reset(token)


class EvaluationBudgetExceeded(RuntimeError):
    pass


class EvaluationClient(LLMClient):
    """Includes formation, repairs, support and grading; unknown usage stays unknown.

    The USD cap is a conservative client-side bound at the frozen peak tariff, not a
    provider billing limit. Unreturned usage retains its full byte/output reservation.
    """
    def __init__(self, settings, recorder, *, max_calls, max_cost_usd, transport=None):
        super().__init__(settings, transport=transport)
        if type(max_calls) is not int or max_calls < 1 or not math.isfinite(max_cost_usd) or max_cost_usd <= 0:
            raise ValueError("Positive request and cost caps are required")
        if settings.main.model not in {"deepseek-flash", "deepseek-v4-pro"}:
            raise ValueError("Unpriced evaluation model")
        self.recorder, self.max_calls, self.max_cost_usd = recorder, max_calls, max_cost_usd
        self.rows, self.reserved_usd = [], 0.0
        self.halt_reason, self.billing_failures = None, 0

    async def _invoke(self, stream, messages, model=None, max_tokens=None, tools=None,
                      **generation):
        selected = model or self.settings.main
        if self.halt_reason:
            raise EvaluationBudgetExceeded(self.halt_reason)
        output = max_tokens if max_tokens is not None else self.settings.max_tokens
        payload = {"messages": messages, "tools": tools, "model": selected.model,
                   "max_tokens": output}
        # UTF-8 byte upper bound plus framing allowance; never discounted for predicted cache.
        upper_tokens = len(canonical(payload).encode("utf-8")) + 1024
        rate = PRICING[selected.model]["peak"]
        reserved = (upper_tokens*rate["miss"] + output*rate["output"]) / 1e6
        if len(self.rows) >= self.max_calls or self.reserved_usd + reserved > self.max_cost_usd:
            self.halt_reason = "request_or_cost_cap"
            raise EvaluationBudgetExceeded("Evaluation request/cost cap reached before send")
        row = {"call": len(self.rows)+1, **context.get(), "model": selected.model,
               "started_at_utc": datetime.now(timezone.utc).isoformat(),
               "role": {"formation": "formation", "independent_judge": "judge"}.get(context.get().get("stage"), "reader"),
               "prompt_sha256": hashlib.sha256(canonical(payload).encode()).hexdigest(),
               "physical_input_chars": len(canonical(payload)), "input_bytes": len(canonical(payload).encode()),
               "cache_layout": layout_of(messages), "usage": None, "error": None,
               "reserved_upper_usd": reserved}
        self.rows.append(row)
        self.reserved_usd += reserved
        self.recorder.append("prompts.jsonl", {**row, "messages": messages, "tools": tools})
        self.recorder.append("events.jsonl", {**row, "event": "request_reserved"})
        started = time.perf_counter()
        try:
            generation.setdefault("temperature", 0)
            generation.setdefault("top_p", 1)
            method = super().chat_stream if stream else super().chat
            result = await method(messages, model=selected, max_tokens=output, tools=tools,
                                  **generation)
            self.billing_failures = 0
            row.update(usage=result.usage or None, raw=result.content,
                       request_budget=getattr(result, "budget", None),
                       finish_reason=result.finish_reason, retries=getattr(result, "retries", None))
            usage = cache_summary([row])
            priced = cost_bounds(usage, selected.model)
            if priced["complete_usage"]:
                actual = priced["estimate"]["peak"]["known_input_output_usd"]
                self.reserved_usd += actual - reserved
                row["known_peak_usd"] = actual
            return result
        except BaseException as exc:
            row["error"] = type(exc).__name__
            from ..observability.memory_trace import execution_failure
            row['failure']=execution_failure(exc,context.get().get('stage','runtime'))
            # Backend errors retain the HTTP code but are not typed HTTP exceptions.
            message = str(exc).lower()
            if any(value in message for value in ("http 402", "insufficient balance", "insufficient_balance")):
                self.billing_failures += 1
                row["billing_failure"] = True
                if self.billing_failures >= 2:
                    self.halt_reason = "repeated_insufficient_balance"
            elif "http 401" in message or "http 403" in message:
                self.halt_reason = "provider_authorization_failed"
            row["halt_reason"] = self.halt_reason
            raise
        finally:
            row["elapsed_ms"] = (time.perf_counter()-started)*1000
            self.recorder.append("calls.jsonl", row)

    async def chat(self, messages, model=None, max_tokens=None, tools=None, **kwargs):
        return await self._invoke(False, messages, model, max_tokens, tools, **kwargs)

    async def chat_stream(self, messages, tools=None, on_delta=None, *, model=None,
                          max_tokens=None, **kwargs):
        return await self._invoke(True, messages, model, max_tokens, tools,
                                  on_delta=on_delta, **kwargs)

    def accounting(self):
        groups = {}
        for row in self.rows:
            groups.setdefault((row.get("arm", "unknown"), row.get("stage", "unknown")), []).append(row)
        return {"all_calls": len(self.rows), "retained_upper_peak_usd": self.reserved_usd,
                "halt_reason": self.halt_reason,
                "cap_usd": self.max_cost_usd, "cap_calls": self.max_calls,
                "groups": [{"arm": arm, "stage": stage, "usage": cache_summary(rows),
                            "cost": cost_bounds(cache_summary(rows), self.settings.main.model)}
                           for (arm, stage), rows in sorted(groups.items())],
                "note": "Frozen price estimate, not invoice. Formation is shared once per visible history; judge cost is separate from inference."}
