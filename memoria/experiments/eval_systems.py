"""Plain full context versus the production memory kernel; public speakers are external authors."""
from __future__ import annotations

import hashlib
import json
import tempfile
import time
from pathlib import Path

from ..claims import canonical
from ..memory.records import KnowledgeAnswer
from ..memory.source_formation import FormRequest
from ..memory.library import SourceInput


class RawContextSystem:
    name = "raw_context"

    def __init__(self, client, *, system, max_tokens=1024, budget_chars=240000):
        self.client, self.system = client, system
        self.max_tokens, self.budget_chars = max_tokens, budget_chars
        self.reset()

    def reset(self, sources=None):
        self._sources = []

    async def ingest(self, source):
        self._sources.append(dict(source))

    def _messages(self, question, sources):
        from ..llm.deepseek_cache import canonical as stable
        evidence = [{"citation": s["id"], "content": s.get("text", ""),
                     "speaker": s.get("speaker", "unknown"),
                     "session_date_raw": s.get("session_date_raw", "")} for s in sources]
        # Complete stable history precedes the question. Gold never decides visibility.
        return [{"role": "system", "content": self.system},
                {"role": "user", "content": stable({"evidence": evidence})},
                {"role": "user", "content": question}]

    async def answer(self, question):
        from ..llm.types import ContextLengthError
        evidence = list(self._sources)
        while True:
            messages = self._messages(question, evidence)
            size = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
            report = await self.client.reader_budget.measure(
                self.client.settings.main, messages, None, self.max_tokens,
                temperature=0, top_p=1)
            if size <= self.budget_chars and report["fits"]:
                break
            if not evidence:
                raise ContextLengthError("Baseline instructions/question exceed full request budget")
            evidence.pop(0)
        started = time.perf_counter()
        result = await self.client.chat(messages, max_tokens=self.max_tokens, temperature=0, top_p=1)
        return {"answer": result.content, "usage": result.usage or None,
                "latency_ms": (time.perf_counter()-started)*1000,
                "retrieved": evidence,
                "extras": {"mode": self.name, "visible_turns": len(evidence),
                           "dropped_turns": len(self._sources)-len(evidence),
                           "full_request_budget": report,
                           "first_retained_id": evidence[0]["id"] if evidence else None,
                           "last_retained_id": evidence[-1]["id"] if evidence else None}}


def source_batches(sources, max_chars=12000):
    """Session windows retain original IDs, speakers and raw clocks.

    The benchmark supplies no timezone: its raw clock is data, not a trusted UTC event time.
    """
    from ..memory.source_units import render_turns
    batch, size, key = [], 0, None
    for source in sources:
        line, _ = render_turns([source])
        if len(line) > max_chars:
            raise ValueError("Single benchmark turn exceeds declared formation window")
        session = source.get("session")
        if batch and (session != key or size + len(line) + 1 > max_chars):
            content, units = render_turns(batch)
            yield {"session": key, "content": content, "source_units": units}
            batch, size = [], 0
        key = session
        batch.append(source)
        size += len(line) + 1
    if batch:
        content, units = render_turns(batch)
        yield {"session": key, "content": content, "source_units": units}


class KernelMemorySystem:
    name = "memoria"

    def __init__(self, kernel_factory, *, max_tokens=1024, budget_chars=8000,
                 write_mode="library", directory=None):
        if write_mode != "library":
            raise ValueError("Public multi-speaker histories must use external library sources")
        self.kernel_factory, self.max_tokens = kernel_factory, max_tokens
        self.budget_chars = budget_chars
        self.directory = Path(directory) if directory else Path(tempfile.mkdtemp(prefix="memoria-eval-"))
        self.directory.mkdir(parents=True, exist_ok=True)
        self.kernel, self._signature = None, None
        self._sources, self._ingested, self._formation = [], set(), []

    def reset(self, sources=None):
        signature = hashlib.sha256(canonical(sources or []).encode()).hexdigest()
        if signature == self._signature:
            return
        if self.kernel is not None:
            self.kernel.store.close()
        self.kernel = self.kernel_factory(self.directory / (signature + ".db"))
        self.session_id = self.kernel.store.create_session("Isolated public benchmark history")["id"]
        self._signature, self._drained, self._formation_error = signature, False, None
        self._sources, self._ingested, self._formation = [], set(), []

    async def ingest(self, source):
        if source["id"] not in self._ingested:
            self._ingested.add(source["id"])
            self._sources.append(dict(source))

    async def drain(self):
        if self._formation_error:
            raise RuntimeError(self._formation_error)
        if self._drained:
            return
        try:
            for index, batch in enumerate(source_batches(self._sources)):
                source = self.kernel.ingest(SourceInput(kind="note", authorship="external",
                    scope="working", title=f"Public conversation session {batch['session']} window {index}",
                    session_id=self.session_id, content=batch["content"], source_units=batch['source_units'],
                    origin="benchmark:public-conversation"))
                receipt = await self.kernel.library.formation.form(source["id"], FormRequest())
                self._formation.append(receipt)
        except Exception as exc:
            self._formation_error = "formation_failed:" + type(exc).__name__
            raise
        self._drained = True

    async def answer(self, question):
        started = time.perf_counter()
        result = await self.kernel.answer(KnowledgeAnswer(query=question, limit=12,
            session_id=self.session_id, budget_chars=self.budget_chars, use_adaptive_retrieval=True))
        ids = [c.get("citation") or c.get("id") for c in result.get("citations", []) if isinstance(c, dict)]
        reason = str(result.get("validation", {}).get("reason") or "")
        adaptive = result.get("access", {}).get("adaptive", {})
        technical_failure = bool(adaptive.get("error_type")) or any(marker in reason for marker in (
            "budget", "reservation", "provider", "invalid_answer_schema", "incomplete_reader", "tool_reader"))
        return {"error": "kernel_execution_failure:"+reason if technical_failure else None,
                "answer": json.dumps({k: result.get(k) for k in
                ("status", "answer")} | {"evidence_ids": [v for v in ids if v]}, ensure_ascii=False),
                "latency_ms": (time.perf_counter()-started)*1000, "usage": None,
                "extras": {"mode": self.name, "stored_turns": len(self._sources),
                           "formation_receipts": self._formation, "kernel_result": result}}

    def close(self):
        if self.kernel is not None:
            self.kernel.store.close()
