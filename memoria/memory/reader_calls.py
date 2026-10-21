"""Request-local Reader telemetry, shared by HTTP, Chat and experiment consumers."""

from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
import time

_request_observer = ContextVar("memoria_reader_request_observer", default=None)
_stage_observer = ContextVar("memoria_memory_stage_observer", default=None)


@contextmanager
def observe_requests(callback, *, timings=None):
    token = _request_observer.set(callback)
    stage_token = _stage_observer.set(timings)
    try:
        yield
    finally:
        _stage_observer.reset(stage_token)
        _request_observer.reset(token)


@contextmanager
def measure_stage(name):
    timings = _stage_observer.get()
    if timings is None:
        yield
    else:
        with timings.measure(name):
            yield


def notify_request():
    callback = _request_observer.get()
    if callback is not None:
        callback()


class ReaderCallLog:
    def __init__(self, client):
        self.client = client
        self.calls = []

    def __getattr__(self, name):
        return getattr(self.client, name)

    async def chat(self, messages, **kwargs):
        started = time.perf_counter()
        attempt = {
            "stage": "reader_pipeline",
            "status": "started",
            "request": {
                "messages": deepcopy(messages),
                "tools": deepcopy(kwargs.get("tools")),
                "max_tokens": kwargs.get("max_tokens"),
            },
            "scope": "LLM client attempt; provider acceptance unknown on failure",
        }
        self.calls.append(attempt)
        try:
            notify_request()
            result = await self.client.chat(messages, **kwargs)
            attempt.update(
                status="returned",
                usage=result.usage or None,
                raw=result.content,
                finish_reason=result.finish_reason,
                retries=getattr(result, "retries", None),
                reader_budget=getattr(result, "budget", None),
            )
            return result
        except BaseException as exc:
            attempt.update(status="failed", error_type=type(exc).__name__, usage=None)
            raise
        finally:
            attempt["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
