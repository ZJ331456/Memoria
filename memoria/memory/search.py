"""Source-aware retrieval and selection; the Kernel only exposes its port."""

import json
from ..claims import sha
from .library import LibraryError
from .records import MemorySearch, READ_POLICY


class MemorySearchService:
    def __init__(self, store, memory, library, entities, readers, dependency_valid):
        self.store = store
        self.memory = memory
        self.library = library
        self.entities = entities
        self.readers = readers
        self.dependency_valid = dependency_valid

    def check_session(self, session_id):
        if session_id is not None and not self.store.session(session_id):
            raise LibraryError(404, "会话不存在")

    async def search(self, request: MemorySearch, *, capture=None, retrieval_query=None):
        """Interleave source ranks, rather than comparing incomparable BM25/cosine scores."""
        self.check_session(request.session_id)
        search_query = retrieval_query or request.query
        entity_enabled = (
            self.memory.entity_enabled if request.use_entities is None else request.use_entities
        )
        identity_lookup = self.entities.lookup(
            request.query, session_id=request.session_id, entity_type=request.entity_type
        )
        if identity_lookup["resolution"]["ambiguous"]:
            if capture is not None:
                capture.update(candidates=[], candidate_dependencies=[])
            packet = {
                "evidence": [],
                "dependencies": [],
                "policy": READ_POLICY,
                "selection": {
                    "strategy": "source_rank_round_robin",
                    "candidate_count": 0,
                    "selected_count": 0,
                    "skipped_budget": 0,
                    "skipped_stale": 0,
                    "reason": "ambiguous_entity_identity",
                },
                "budget": {
                    "unit": "serialized_json_chars",
                    "limit": request.budget_chars,
                    "used": 0,
                },
            }
            for _ in range(4):
                packet["budget"]["used"] = len(json.dumps(packet, ensure_ascii=False))
            if packet["budget"]["used"] > request.budget_chars:
                raise LibraryError(413, "实体澄清元数据超过预算")
            return packet
        lanes = {}
        for domain in request.domains:
            if domain == "memory":
                items = await self.memory.retrieve(
                    search_query,
                    request.limit,
                    mode=request.memory_mode,
                    as_of=request.recorded_as_of,
                    use_entities=entity_enabled,
                    session_id=request.session_id,
                    entity_type=request.entity_type,
                )
                lanes[domain] = [self.readers.memory(item, search_query) for item in items]
            elif domain == "conversation":
                items = self.store.search_messages(search_query, request.limit)
                lanes[domain] = [
                    record
                    for item in items
                    if (record := self.readers.conversation(item, search_query))
                ]
            else:
                items = self.library.search(
                    search_query,
                    session_id=request.session_id,
                    limit=min(8, request.limit),
                    budget=8000,
                    chunks_per_source=2,
                )
                if entity_enabled:
                    items = self.library.entity_supplement(
                        request.query,
                        items,
                        session_id=request.session_id,
                        entity_type=request.entity_type,
                        limit=min(8, request.limit),
                    )
                consolidated = self.library.formation.search(
                    search_query, session_id=request.session_id, limit=min(4, request.limit)
                )
                mixed = []
                for rank in range(max(len(items), len(consolidated))):
                    for channel in (consolidated, items):
                        if rank < len(channel):
                            mixed.append(channel[rank])
                lanes[domain] = [self.readers.library_hit(item) for item in mixed[: request.limit]]
        from .temporal_evidence import filter_records, references

        temporal_rejected = []
        for domain, lane in lanes.items():
            from dataclasses import replace

            filtered, reasons = filter_records(lane, request)
            temporal_rejected.extend(reasons)
            lanes[domain] = [
                (
                    replace(
                        r,
                        dependency={
                            **r.dependency,
                            "observation_refs": references(
                                r.provenance["observations"],
                                (
                                    "conversation"
                                    if r.domain in {"memory", "conversation"}
                                    else "library"
                                ),
                            ),
                        },
                    )
                    if r.provenance.get("observations")
                    else r
                )
                for r in filtered
            ]
        identity_rejected = []
        if identity_lookup["resolution"]["entity_ids"]:
            from dataclasses import replace
            from .entities import Anchor

            for domain, lane in lanes.items():
                retained = []
                for record in lane:
                    try:
                        if record.representation == "consolidated_memory":
                            identity_rejected.append(
                                {"id": record.source_id, "reason": "entity_identity_unestablished"}
                            )
                            continue
                        if domain == "library":
                            a = Anchor(
                                domain="library",
                                source_id=record.source_id,
                                revision_id=record.dependency["revision_id"],
                                start=record.start,
                                end=record.end,
                                quote=record.content,
                                sha256=record.dependency["sha"],
                            )
                        else:
                            source = (
                                self.store.memory_source(record.source_id)
                                if domain == "memory"
                                else self.store.message_source(record.source_id)
                            )
                            if not source:
                                raise LibraryError(409, "No original source")
                            text = source["content"]
                            a = Anchor(
                                domain="conversation",
                                source_id=source["message_id"],
                                start=0,
                                end=min(4000, len(text)),
                                quote=text[:4000],
                                sha256=sha(text),
                            )
                        bound = self.entities.source_binding(identity_lookup, a)
                        if not bound["dependency"]:
                            identity_rejected.append(
                                {
                                    "id": record.source_id,
                                    "reason": "entity_identity_" + bound["status"],
                                }
                            )
                            continue
                        retained.append(
                            replace(
                                record,
                                dependency={
                                    **record.dependency,
                                    "entity_lookup": bound["dependency"],
                                },
                                provenance={
                                    **record.provenance,
                                    "entity_identity": self.entities.evidence(bound["dependency"]),
                                },
                            )
                        )
                    except LibraryError:
                        identity_rejected.append(
                            {"id": record.source_id, "reason": "entity_source_unavailable"}
                        )
                lanes[domain] = retained
        response = {
            "evidence": [],
            "dependencies": [],
            "policy": READ_POLICY,
            "selection": {
                "strategy": "source_rank_round_robin",
                "candidate_count": sum(map(len, lanes.values())),
                "selected_count": 0,
                "skipped_budget": 0,
                "skipped_stale": 0,
                "identity_rejected": identity_rejected,
                "temporal_rejected": temporal_rejected,
            },
            "budget": {"unit": "serialized_json_chars", "limit": request.budget_chars, "used": 0},
        }

        def size():
            # Count the complete JSON response, including provenance, quotes, dependency snapshots and trace.
            for _ in range(4):
                response["budget"]["used"] = len(json.dumps(response, ensure_ascii=False))
            return response["budget"]["used"]

        seen = set()
        if capture is not None:
            capture["candidates"] = [r.public() for lane in lanes.values() for r in lane]
            capture["candidate_dependencies"] = [
                r.dependency for lane in lanes.values() for r in lane
            ]
            capture["identity_rejected"] = list(identity_rejected)
        response["selection"]["identity_rejected_count"] = len(identity_rejected)
        response["selection"]["temporal_rejected_count"] = len(temporal_rejected)
        response["selection"]["temporal_diagnostics_truncated"] = False
        while response["selection"]["temporal_rejected"] and size() > request.budget_chars - 32:
            response["selection"]["temporal_rejected"].pop()
            response["selection"]["temporal_diagnostics_truncated"] = True
        response["selection"]["identity_diagnostics_truncated"] = False
        while response["selection"]["identity_rejected"] and size() > request.budget_chars - 32:
            response["selection"]["identity_rejected"].pop()
            response["selection"]["identity_diagnostics_truncated"] = True
        if size() > request.budget_chars:
            raise LibraryError(413, "身份保护元数据超过预算")
        with self.store.lock:
            self.check_session(
                request.session_id
            )  # Vector calls may have yielded while a session was deleted.
            for rank in range(max(map(len, lanes.values()), default=0)):
                for domain in request.domains:
                    if rank >= len(lanes[domain]) or len(response["evidence"]) >= request.limit:
                        continue
                    record = lanes[domain][rank]
                    key = (record.domain, record.source_id, record.start, record.end)
                    if key in seen:
                        continue
                    if not self.dependency_valid(record.dependency, session_id=request.session_id):
                        response["selection"]["skipped_stale"] += 1
                        continue
                    seen.add(key)
                    evidence = record.public() | {"source_rank": rank + 1}
                    response["evidence"].append(evidence)
                    new_dep = record.dependency not in response["dependencies"]
                    if new_dep:
                        response["dependencies"].append(record.dependency)
                    response["selection"]["selected_count"] = len(response["evidence"])
                    if (
                        size() > request.budget_chars - 32
                    ):  # Reserve space for final counter digit changes.
                        response["evidence"].pop()
                        if new_dep:
                            response["dependencies"].pop()
                        response["selection"]["skipped_budget"] += 1
            response["selection"]["selected_count"] = len(response["evidence"])
            size()
        from ..observability.memory_trace import record_read

        record_read(
            request.model_dump(mode="json"),
            [r.public() for lane in lanes.values() for r in lane],
            response["evidence"],
        )
        return response
