"""Tenant-bound memory application facade. Consumers use this port, not legacy paths."""

from .access import MemoryAccessOrchestrator
from .library import SourceInput, LibraryError
from .records import Domain, MemorySearch, EvidenceRecord


class MemoryKernel:
    def __init__(self, settings, store, llm=None, *, user_id="local"):
        from .bootstrap import MemoryComponents
        from .publication_policy import PublicationPolicy
        from .search import MemorySearchService

        components = MemoryComponents(settings, store, llm, user_id=user_id)
        self.settings, self.store, self.llm = settings, store, llm
        self.claims = components.claims
        self.markdown = components.markdown
        self.embedder = components.embedder
        self.memory = components.memory
        self.episodes = components.episodes
        self.auto_review = components.auto_review
        self.formation = components.formation
        self.library = components.library
        self.entities = components.entities
        self.readers = components.readers
        self.memory_worker = components.memory_worker
        self.personal_access = MemoryAccessOrchestrator(self.claims, self.memory, llm)
        self.publication_policy = PublicationPolicy(self.claims)
        self.queries = MemorySearchService(
            store, self.memory, self.library, self.entities, self.readers, self.dependency_valid
        )

    def access_state(self, request):
        return self.guard_pending_revisions(self.claims.access(request), request)

    def configure_embedding(self):
        from .embedding import EmbeddingClient

        replacement = EmbeddingClient(
            self.settings.embedding,
            min(self.settings.request_timeout_seconds, 30),
            self.settings.max_retries,
        )
        with self.store.lock:
            if replacement.enabled:
                self.store.configure_embedding(replacement.namespace)
            self.embedder = replacement
            self.memory.embedder = replacement

    async def recall(self, query, limit=5, kinds=None, *, trace_query=None, **options):
        from ..observability.memory_trace import record_read

        items = await self.memory.retrieve(query, limit, kinds, **options)
        records = [self.readers.memory(item, trace_query or query).public() for item in items]
        record_read({"query": query, "route": "memory_recall"}, records, records)
        return items

    def revalidate_answer(self, request, result, *, answer=None):
        from .publication import revalidate

        return revalidate(self, request, result, answer=answer)

    async def remember(self, content, kind="fact", importance=3, source="agent_tool",
                       source_ref=None, *, require_source=False):
        return await self.memory.remember(
            content, kind, importance, source, source_ref, require_source=require_source
        )

    def forget(self, memory_id):
        deleted = self.store.delete_memory(memory_id)
        self.memory.refresh_markdown()
        return deleted

    def ingest(self, source: SourceInput):
        """Store a typed non-conversation source; never invent a user message or approve a personal fact."""
        return self.library.create(source)

    def ingest_file(self, filename: str, data: bytes, **metadata):
        return self.library.files.ingest(filename, data, **metadata)

    # --- Source-domain management facade: API adapters call these, never
    # kernel.library internals. Domain logic and transactions stay in library. ---

    def sources(self, limit: int = 50):
        return self.library.list(limit)

    def search_sources(self, query: str, *, session_id: str | None = None,
                       limit: int = 5, budget: int = 4000, chunks_per_source: int = 1):
        return self.library.search(query, session_id=session_id, limit=limit,
                                   budget=budget, chunks_per_source=chunks_per_source)

    def source_detail(self, sid: str):
        return self.library.detail(sid)

    def revise_source(self, sid: str, body):
        return self.library.revise(sid, body)

    def source_version(self, sid: str, rid: str):
        return self.library.version(sid, rid)

    def retract_source(self, sid: str):
        return self.library.retract(sid)

    def source_feedback(self, sid: str, body):
        return self.library.feedback(sid, body)

    async def distill_source(self, sid: str):
        return await self.library.distill(sid)

    async def form_source(self, sid: str, body):
        return await self.library.formation.form(sid, body)

    def source_observations(self, sid: str):
        return self.library.formation.observations(sid)

    async def consolidate_observation(self, oid: str):
        return await self.library.formation.consolidate(oid)

    def consolidations(self, session_id: str | None = None):
        if session_id is not None and not self.store.session(session_id):
            from .library import LibraryError
            raise LibraryError(404, "会话不存在")
        return self.library.formation.list(session_id=session_id)

    def retire_consolidation(self, key: str, expected_revision_id: str, session_id: str | None = None):
        return self.library.formation.retire(key, expected_revision_id, session_id=session_id)

    def original_source_file(self, sid: str, revision_id: str):
        return self.library.files.original(sid, revision_id)

    def record_message(
        self, session_id: str, role: str, content: str, *, source_time: str | None = None
    ):
        """Native conversation adapter; provenance continues to come from trusted message_ingress."""
        if role not in {"user", "assistant", "system", "tool"}:
            raise LibraryError(422, "消息角色非法")
        if not self.store.session(session_id):
            raise LibraryError(404, "会话不存在")
        return self.store.add_message(session_id, role, content, source_time=source_time)

    async def access(self, request):
        """Current/historical personal state keeps the existing Claim/time/condition authority."""
        from .reader_calls import measure_stage

        with measure_stage("claim.access"):
            result = await self.personal_access.access(request)
            return self.guard_pending_revisions(result, request)

    def guard_pending_revisions(self, result, request):
        return self.publication_policy.guard(result, request)

    def lineage(self, references):
        from ..observability.memory_trace import source_lineage

        return source_lineage(self, references)

    async def answer(self, request, *, unified_head=False):
        """Production Reader, never an alternate benchmark extractor or Claim promotion.

        `unified_head` puts the Reader on the shared head: a fixed system, the data block at
        a constant offset and the trusted contract repeated after it. It is opt-in because it
        changes the instruction position, which is a prompt change that needs its own check.
        """
        from .access import QueryRequest
        from .reader import read_memory_answer, read_knowledge_answer
        from ..observability.memory_trace import answer_trace
        if self.llm is None:
            raise LibraryError(503, "未配置Reader模型")
        from .evidence.query_plan import complex_recall

        adaptive = getattr(request, "use_adaptive_retrieval", False)
        if adaptive is None:
            adaptive = (
                complex_recall(request.query)
                and not getattr(request, "use_llm_rerank", False)
                and not getattr(request, "use_query_model", False)
            )
        if adaptive:
            if unified_head:
                raise LibraryError(422, "unified_head is only supported by the non-adaptive personal Reader")
            from .adaptive_retrieval import read_adaptive

            if getattr(request, "use_llm_rerank", False) or getattr(
                request, "use_query_model", False
            ):
                raise LibraryError(
                    422, "自适应读取与独立查询模型/重排不能同时启用；所有调用使用同一预算"
                )
            result = await read_adaptive(self, request)
            result = self.revalidate_answer(request, result)
            result["closed_loop"] = answer_trace(self, request.model_dump(mode="json"), result)
            return result
        if isinstance(request, QueryRequest):
            result = await read_memory_answer(
                self.claims,
                self.memory,
                self.llm,
                request,
                accessor=self.access,
                policy_guard=lambda access: bool(
                    self.guard_pending_revisions(dict(access), request).get("pending_revisions")
                ),
                unified_head=unified_head,
            )
            result["access"]["pending_revisions"] = self.guard_pending_revisions(
                dict(result["access"]), request
            ).get("pending_revisions", [])
        elif isinstance(request, MemorySearch):
            if unified_head:
                raise LibraryError(422, "unified_head is only supported by the non-adaptive personal Reader")
            if request.domains != ["library"]:
                raise LibraryError(
                    422, "资料Reader仅接受library域；个人状态使用QueryRequest，其他来源使用search"
                )
            result = await read_knowledge_answer(self, request)
        else:
            raise TypeError(
                "Use QueryRequest for personal state or MemorySearch for source knowledge"
            )
        result = self.revalidate_answer(request, result)
        result["closed_loop"] = answer_trace(
            self, request.model_dump(mode="json"), result, calls=result.get("reader_calls")
        )
        return result

    def dependency_valid(self, dependency: dict, *, session_id: str | None = None):
        if not isinstance(dependency, dict) or not dependency.get("id"):
            return False
        try:
            if dependency.get("entity_lookup") and not self.entities.dependency_valid(
                dependency["entity_lookup"]
            ):
                return False
            dependency = {k: v for k, v in dependency.items() if k != "entity_lookup"}
            from .temporal_evidence import valid

            if not valid(self.store, dependency.get("observation_refs", []), self.library):
                return False
            kind = dependency.get("dependency_type", "memory")
            if kind == "library_consolidation":
                return self.library.formation.dependency_valid(dependency, session_id=session_id)
            if kind == "library":
                return self.library.dependency_valid_for_session(dependency, session_id=session_id)
            if kind == "claim_access":
                if not self.claims.access_dependency_valid(dependency):
                    return False
                from ..claims import AccessRequest

                request = AccessRequest.model_validate(dependency["request"])
                with self.store.lock:
                    current = {
                        "path": "retrieve",
                        "reason": "dependency_check",
                        "payload": self.claims.selection_snapshot(request),
                    }
                return not self.guard_pending_revisions(current, request).get("pending_revisions")
            if kind not in {"memory", "message"}:
                return False
            return self.store.memory_dependency_valid(dependency)
        except (KeyError, TypeError, ValueError, LibraryError):
            return False

    def read(self, domain: Domain, source_id: str, *, session_id: str | None = None):
        """Read a current source through its adapter. Explicit old library versions use the history API."""
        self.check_session(session_id)
        with self.store.lock:
            record = None
            if domain == "conversation":
                record = self.readers.conversation({"id": source_id}, full=True)
            elif domain == "memory":
                item = self.store.memory(source_id)
                if item:
                    record = self.readers.memory(item, full=True)
            elif domain == "library" and source_id.startswith("consolidation:"):
                record = self.readers.library_hit(
                    self.library.formation.read(
                        source_id.removeprefix("consolidation:"), session_id=session_id
                    )
                )
            elif domain == "library":
                source = self.library.detail(source_id)
                revision, feedback = source["revision"], source["feedback"]
                record = EvidenceRecord(
                    "library",
                    source_id,
                    "source_quote",
                    revision["content"],
                    0,
                    len(revision["content"]),
                    f"[library:{revision['id']}:0:{len(revision['content'])}]",
                    {
                        "kind": source["kind"],
                        "title": revision["title"],
                        "authorship": source["authorship"],
                        "scope": source["scope"],
                        "revision_id": revision["id"],
                        "sha": revision["sha"],
                    },
                    {
                        "dependency_type": "library",
                        "id": source_id,
                        "revision_id": revision["id"],
                        "sha": revision["sha"],
                        "feedback_id": feedback["id"] if feedback else None,
                    },
                )
            if not record or not self.dependency_valid(record.dependency, session_id=session_id):
                raise LibraryError(404, "来源不存在、已失效或不适用于当前作用域")
            return {"evidence": record.public(), "dependency": record.dependency}

    def check_session(self, session_id):
        return self.queries.check_session(session_id)

    async def search(self, request: MemorySearch, *, capture=None, retrieval_query=None):
        return await self.queries.search(request, capture=capture, retrieval_query=retrieval_query)
