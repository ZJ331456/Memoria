"""Request-local timers; nested durations are inclusive and must not be summed."""
from __future__ import annotations

import time
from contextlib import contextmanager


class StageTimings:
    def __init__(self):
        self.stages: dict[str, dict] = {}

    @contextmanager
    def measure(self, name: str):
        started = time.perf_counter()
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            row = self.stages.setdefault(name, {"calls": 0, "duration_ms": 0.0, "failed": 0})
            row["calls"] += 1
            row["duration_ms"] += (time.perf_counter() - started) * 1000
            row["failed"] += int(failed)

    def public_dict(self):
        return {"scope": "inclusive elapsed wall time; nested stages overlap", "stages": {
            name: {**row, "duration_ms": round(row["duration_ms"], 3)} for name, row in self.stages.items()}}

    def since(self, name: str, started: float):
        row = self.stages.setdefault(name, {"calls": 0, "duration_ms": 0.0, "failed": 0})
        row["calls"] += 1
        row["duration_ms"] += (time.perf_counter() - started) * 1000
