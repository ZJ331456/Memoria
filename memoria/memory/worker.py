from __future__ import annotations

import asyncio
import logging
import uuid
import time
from contextlib import suppress

from ..llm import LLMClient
from ..store import Store
from .engine import MemoryEngine

logger = logging.getLogger(__name__)


class MemoryJobWorker:
    """Durable, retryable post-turn extraction and consolidation worker."""

    def __init__(self, store: Store, llm: LLMClient, memory: MemoryEngine, poll_seconds: float = 0.5, lease_seconds: int = 180, max_retries: int = 3, backoff_seconds: int = 5):
        self.store, self.llm, self.memory = store, llm, memory
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.owner = uuid.uuid4().hex

    async def process_once(self) -> bool:
        job = self.store.claim_memory_job(self.owner, self.lease_seconds, self.max_retries)
        if not job:
            return False
        heartbeat = asyncio.create_task(self._heartbeat(job["id"]))
        try:
            extracted = await self.llm.extract_memories(job["user_text"], job["assistant_text"])
            self.store.stage_memory_reviews(job["id"], self.owner, extracted)
            self.store.finish_memory_job(job["id"], owner=self.owner, max_retries=self.max_retries, backoff_seconds=self.backoff_seconds)
        except asyncio.CancelledError:
            self.store.finish_memory_job(job["id"], "worker cancelled", self.owner, self.max_retries, self.backoff_seconds)
            raise
        except Exception as exc:
            logger.exception("记忆后台任务失败 job=%s", job["id"])
            self.store.finish_memory_job(job["id"], f"{type(exc).__name__}: {exc}", self.owner, self.max_retries, self.backoff_seconds)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        return True

    async def run(self) -> None:
        next_index = 0.0
        next_experience_dense, dense_failures = 0.0, 0
        while True:
            processed = await self.process_once()
            if time.monotonic() >= next_index:
                await self.memory.reindex(limit=16)
                from .experience import reindex
                await asyncio.to_thread(reindex, self.store, 16)
                from .experience import reindex_dense
                if time.monotonic()>=next_experience_dense:
                    try:
                        await reindex_dense(self.store, getattr(self.memory,"embedder",None), 16)
                        dense_failures=0
                        next_experience_dense=time.monotonic()+15
                    except Exception as exc:
                        dense_failures+=1
                        next_experience_dense=time.monotonic()+min(300,5*2**min(dense_failures,6))
                        logger.warning("经历向量维护失败 type=%s",type(exc).__name__)
                next_index = time.monotonic()+15
            if not processed:
                await asyncio.sleep(self.poll_seconds)

    async def _heartbeat(self, job_id: str) -> None:
        while True:
            await asyncio.sleep(max(5, self.lease_seconds // 3))
            if not self.store.renew_memory_job(job_id, self.owner, self.lease_seconds):
                return
