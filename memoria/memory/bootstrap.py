"""Tenant-bound memory services; no chat runtime, HTTP server or tool registry required."""

from __future__ import annotations


from ..claims import ClaimMemory
from .auto_review import AutoMemoryReviewer
from .embedding import EmbeddingClient
from .engine import MemoryEngine
from .episodic import EpisodicMemory
from .formation import MemoryFormation
from .layer import MarkdownMemoryLayer
from .library import EvidenceLibrary
from .records import SourceReaders
from .worker import MemoryJobWorker


class MemoryComponents:
    """Explicit composition root. Construction never starts background tasks."""

    def __init__(self, settings, store, llm, *, user_id):
        self.settings = settings
        self.store = store
        self.llm = llm
        self.claims = ClaimMemory(store, user_id)
        self.markdown = MarkdownMemoryLayer(settings.markdown_directory, settings.markdown_enabled)
        self.embedder = EmbeddingClient(
            settings.embedding, min(settings.request_timeout_seconds, 30), settings.max_retries
        )
        self.memory = MemoryEngine(
            store,
            self.embedder,
            getattr(llm, "decide_memory_relation", None),
            settings.vector_scan_limit,
            markdown=self.markdown,
        )
        self.memory.refresh_markdown()
        layers = getattr(settings, "memory_layers", None)
        self.episodes = (
            EpisodicMemory(store, layers)
            if layers and layers.enabled and layers.episodic_enabled
            else None
        )
        self.auto_review = (
            AutoMemoryReviewer(store, self.claims, self.memory, llm, settings.memory_approval_mode)
            if llm
            else None
        )
        formation = getattr(settings, "memory_formation", None)
        self.formation = (
            MemoryFormation(store, llm, formation, self.auto_review)
            if llm and formation and formation.enabled
            else None
        )
        self.library = EvidenceLibrary(store, llm, settings)
        self.entities = store.entities
        self.memory.entity_enabled = settings.entity_retrieval_enabled
        self.readers = SourceReaders(store, self.library)
        self.memory_worker = (
            MemoryJobWorker(
                store,
                llm,
                self.memory,
                lease_seconds=settings.memory_job_lease_seconds,
                max_retries=settings.memory_job_max_retries,
                backoff_seconds=settings.memory_job_backoff_seconds,
                reviewer=self.auto_review,
                formation=self.formation,
            )
            if llm
            else None
        )
