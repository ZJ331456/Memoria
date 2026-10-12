"""Authenticated unified read and diagnostic access; no automatic state promotion."""
from __future__ import annotations

import time
import re
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from ..claims import AccessRequest, ClaimError, ClaimMemory, Strict, canonical, conditions, sha, timestamp
from ..observability.timing import StageTimings
from ..store import normalize_time
from .evidence_selector import select_evidence
from .temporal_query import TemporalQueryParser, parse_query
from .evidence import EvidenceBudget, requirements_for
from .evidence.requirements import coverage


def infer_query(text: str) -> QueryRequest | None:
    """Only short personal-state questions. General tasks keep their existing planner."""
    from ..claims import infer_access
    if infer_access(text):
        return QueryRequest(query=text)
    if len(text) > 160 or re.search(r"请记住|帮我|写代码|生成|翻译|总结", text):
        return None
    parsed = parse_query(text)
    if parsed["predicates"] and re.search(r"我|我的", text) and re.search(r"哪里|在哪|什么|何时|什么时候|哪种|哪所|有哪些|是多少", text):
        return QueryRequest(query=text)
    return None


class QueryRequest(Strict):
    query: str = Field(min_length=1, max_length=1000)
    subject_id: str = Field(default="self", pattern=r"^(self|entity:[a-zA-Z0-9_-]{1,64})$")
    predicates: list[str] = Field(default_factory=list, max_length=12)
    context: dict = Field(default_factory=dict)
    scope_mode: Literal["auto", "applicable", "default"] = "auto"
    fact_as_of: str | None = None
    recorded_as_of: str | None = None
    mode: Literal["unified", "claim", "keyword", "bm25", "dense", "rrf"] = "unified"
    selector: Literal["topk", "coverage", "mmr", "evidence_coverage"] = "coverage"
    budget_chars: int = Field(default=6000, ge=256, le=16000, strict=True)
    budget_unit: Literal["chars", "utf8_bytes", "reader_tokens"] = "chars"
    evidence_budget: int | None = Field(default=None, ge=256, le=16000, strict=True)
    top_k: int = Field(default=8, ge=1, le=32, strict=True)
    candidate_limit: int = Field(default=32, ge=1, le=64, strict=True)
    use_query_model: bool = Field(default=False, strict=True)

    _conditions = field_validator("context")(conditions)
    _times = field_validator("fact_as_of", "recorded_as_of")(timestamp)

    @field_validator("predicates")
    @classmethod
    def slots(cls, value):
        return AccessRequest.slots(value) if value else []


class MemoryAccessOrchestrator:
    def __init__(self, claims: ClaimMemory, engine, llm=None, reader_tokenizer=None):
        self.claims, self.engine, self.llm = claims, engine, llm
        self.reader_tokenizer = reader_tokenizer

    def timeline(self, request: QueryRequest, parsed: dict) -> list[dict]:
        """Source-valid revisions; proposals are labelled and never turned into states."""
        items = []
        start = normalize_time(parsed["range_start"]) if parsed["range_start"] else None
        end = normalize_time(parsed["range_end"]) if parsed["range_end"] else None
        with self.claims.store._memory_transaction():
            roots = self.claims.db.execute("SELECT id FROM claim_roots WHERE user_id=? AND subject_id=? ORDER BY created_at DESC LIMIT 500",
                                          (self.claims.user_id, request.subject_id)).fetchall()
            for row in roots:
                history = self.claims.history(row[0])
                if parsed["axis"] == "recorded" and start:
                    # Baseline immediately BEFORE the interval plus revisions inside it.
                    # Never use the present head to invent past knowledge.
                    from datetime import timedelta
                    before = (datetime.fromisoformat(start)-timedelta(microseconds=1)).isoformat()
                    baseline = self.claims._head(row[0], effective=True, recorded=before)
                    baseline_id = baseline["id"] if baseline and baseline["recorded_at"] < start else None
                else:
                    baseline_id = None
                for item in history:
                    if parsed["predicates"] and item["predicate"] not in parsed["predicates"]:
                        continue
                    if request.recorded_as_of and item["recorded_at"] > request.recorded_as_of:
                        continue
                    if not item["source_available"]:
                        continue
                    scope = item["scope"]
                    if parsed["scope_mode"] == "default" and scope:
                        continue
                    if any(k in parsed["context"] and (type(parsed["context"][k]) != type(v) or parsed["context"][k] != v) for k, v in scope.items()):
                        continue
                    # Unknown bounds stay visible as unknown, not evidence of residence in a year.
                    if parsed["axis"] == "recorded" and start:
                        at = item["recorded_at"]
                        if not (start <= at < end or item["revision_id"] == baseline_id):
                            continue
                    if parsed["axis"] == "fact" and start and item.get("fact_end") and item["fact_end"] <= start:
                        continue
                    if parsed["axis"] == "fact" and end and item.get("fact_start") and item["fact_start"] >= end:
                        continue
                    if parsed["intent"] == "transition" and parsed.get("transition_from") and parsed.get("transition_to"):
                        quotes = " ".join(e["quote"] for e in item["evidence"])
                        if not any(anchor in item["value"] or anchor in quotes for anchor in (parsed["transition_from"], parsed["transition_to"])):
                            continue
                    cutoff = request.recorded_as_of or (end if parsed["axis"] == "recorded" else None)
                    if cutoff and item.get("retired_at") and item["retired_at"] >= cutoff:
                        item["retired_at"] = None
                    if request.recorded_as_of and item.get("retired_at") and item["retired_at"] > request.recorded_as_of:
                        item["retired_at"] = None  # Do not expose a future correction timestamp.
                    item["historical_state_verified"] = False
                    items.append(item)
                    if len(items) >= 500:
                        return items
        return items

    async def access(self, request: QueryRequest) -> dict:
        started, timings = time.perf_counter(), StageTimings()
        try:
            tokenizer = self.reader_tokenizer
            if request.budget_unit == "reader_tokens" and tokenizer is None:
                from .evidence.reader_tokenizer import for_reader
                tokenizer = for_reader(self.llm)
            budget = EvidenceBudget(request.evidence_budget or request.budget_chars, request.budget_unit, tokenizer)
        except ValueError as exc:
            raise ClaimError(422, str(exc)) from exc
        parsed = TemporalQueryParser().parse(request.query)
        routing = {"origin":"rules", "model_calls":0}
        if request.use_query_model:
            from .query_router import route_slots
            with timings.measure("access.query_model"):
                routing = await route_slots(self.llm, request.query, parsed)
        inferred_context = dict(parsed["context"])
        parsed.update(subject_id=request.subject_id, predicates=request.predicates or parsed["predicates"],
                      context={**parsed["context"], **request.context})
        if request.scope_mode != "auto":
            if request.scope_mode != parsed["scope_mode"] and parsed["scope_mode"] == "default":
                parsed.update(requires_clarification=True, reason="explicit_scope_conflicts_with_query")
            parsed["scope_mode"] = request.scope_mode
        if parsed["scope_mode"] == "default" and parsed["context"]:
            parsed.update(requires_clarification=True, reason="default_scope_with_specific_context")
        if any(k in request.context and (type(request.context[k]) != type(v) or request.context[k] != v) for k, v in inferred_context.items()):
            parsed.update(requires_clarification=True, reason="explicit_context_conflicts_with_query")
        # Ambiguous entity wording needs explicit pseudonymous identity, never guessed self.
        if parsed["subject_requires_clarification"] and request.subject_id != "self":
            parsed["subject_requires_clarification"] = False
            if parsed["reason"] == "subject_requires_explicit_entity_id":
                parsed.update(requires_clarification=False, reason="explicit_subject")
        explicit_time = bool(request.fact_as_of or request.recorded_as_of)
        if explicit_time and not parsed["subject_requires_clarification"] and not parsed["condition_requires_clarification"] and parsed["reason"] not in {
                "explicit_context_conflicts_with_query", "explicit_scope_conflicts_with_query",
                "default_scope_with_specific_context", "default_and_specific_context_ambiguous", "multiple_query_conditions"}:
            parsed.update(intent="history", requires_clarification=False, precision="exact",
                          range_start=None, range_end=None, reason="explicit_as_of")
            parsed.update(fact_as_of=request.fact_as_of, recorded_as_of=request.recorded_as_of,
                          fact_interval=None, recorded_interval=None, time_precision="exact")
            if request.recorded_as_of and not request.fact_as_of:
                parsed["axis"] = "recorded"
            parsed["temporal_intent"] = "historical_knowledge" if parsed["axis"]=="recorded" else "historical_fact"
        parsed["predicate"] = parsed["predicates"][0] if len(parsed["predicates"])==1 else None
        candidates, rejected, state, timeline = [], [], None, []
        history_range = bool(parsed["range_start"]) or parsed["intent"] == "transition"
        state_mode = request.mode in {"unified", "claim"}
        if parsed["requires_clarification"]:
            path, reason = "clarify", parsed["reason"]
        elif history_range and not state_mode:
            path, reason = "clarify", "source_lane_requires_explicit_fact_instant; calendar_range_not_supported"
        elif history_range:
            with timings.measure("access.timeline"):
                timeline = self.timeline(request, parsed)
            path, reason = "history", "revision_timeline; not a point-in-time answer"
            for item in timeline:
                candidates.append({"id": item["revision_id"], "kind": "timeline", "score": 1,
                                   "covers": [item["predicate"]], "source_ids": item["support_group_ids"], "data": item})
        else:
            if parsed["predicates"] and state_mode:
                args = AccessRequest(subject_id=request.subject_id, predicates=parsed["predicates"], context=parsed["context"],
                                     scope_mode=parsed["scope_mode"],
                                     fact_as_of=request.fact_as_of, recorded_as_of=request.recorded_as_of,
                                     intent="history" if explicit_time else "current",
                                     budget_chars=request.budget_chars if request.budget_unit=="chars" else 16000)
                with timings.measure("access.claims"):
                    state = self.claims.access(args)
                path, reason = state["path"], state["reason"]
            else:
                path, reason = "retrieve", "general_source_query"
            supplement = request.mode not in {"claim"} and (request.mode != "unified" or not state or not state["payload"]["complete"])
            # Raw memories have no recorded-time dimension. Never use them to answer past knowledge.
            if supplement and not request.recorded_as_of:
                diag = {}
                with timings.measure("access.semantic"):
                    memories = await self.engine.retrieve(request.query, limit=request.candidate_limit, as_of=request.fact_as_of,
                                                          expand_graph=False, mode="rrf" if request.mode == "unified" else request.mode,
                                                          diagnostics=diag, timings=timings)
                # Model I/O may have permitted a correction/revocation; take a fresh consistent snapshot.
                with self.claims.store._memory_transaction():
                    if state:
                        state = self.claims.access(args)
                        path, reason = state["path"], state["reason"]
                    for item in memories:
                        current = self.claims.store.memory(item["id"])
                        if not current or current["content"] != item["content"] or current["updated_at"] != item["updated_at"]:
                            rejected.append({"id": item["id"], "reason": "memory_changed_during_retrieval"})
                            continue
                        intervals = self.claims.store.memory_validity(item["id"])
                        fact = request.fact_as_of or datetime.now().astimezone().isoformat()
                        fact = normalize_time(fact)
                        if not any(i["valid_at"] <= fact and (i["invalid_at"] is None or fact < i["invalid_at"]) for i in intervals):
                            rejected.append({"id": item["id"], "reason": "inactive_at_requested_time"})
                            continue
                        ref = current.get("source_ref")
                        source = self.claims.store.message_source(ref) if ref else None
                        try:
                            if not source or not source["content"]:
                                raise ClaimError(422, "no source")
                            text = source["content"]
                            evidence = {"message_id": ref, "start": 0, "end": len(text), "content_sha256": sha(text)}
                            self.claims._evidence({"evidence": [evidence]})
                        except ClaimError:
                            rejected.append({"id": item["id"], "reason": "source_unavailable_revoked_or_public"})
                            continue
                        candidates.append({"id": item["id"], "kind": "source", "score": item["retrieval"]["score"],
                                           "covers": [], "source_ids": [ref], "data": {
                                               "memory_id": item["id"], "text": text,
                                               "unverified_retrieval_hint": item.get("retrieval_excerpt", item["content"]),
                                               "evidence": {**evidence, "quote": text}, "retrieval": item["retrieval"],
                                               "approved_personal_state": False, "semantic_support_verified": False}})
                reason += ("; source supplement does not resolve missing/conflicting Claim state" if state else "; source candidates remain unapproved")
            else:
                diag = {"skipped": "recorded_time_unavailable_for_semantic_memories" if request.recorded_as_of else "complete_view_or_claim_only"}
            if state:
                # Put every condition/polarity of a slot in one indivisible unit.
                for predicate in parsed["predicates"]:
                    group = [c for c in state["payload"]["claims"] if c["predicate"] == predicate]
                    if group:
                        candidates.insert(0, {"id": "claim:" + predicate, "kind": "claim", "score": 2,
                                              "covers": [predicate], "source_ids": sorted({e["message_id"] for c in group for e in c["evidence"]}), "data": group})
        requirements = requirements_for(request.query, parsed)
        enriched = [{**c, "covers":coverage(c, requirements, parsed)} for c in candidates]
        comparison = {}
        with timings.measure("access.selection"):
            for strategy in ("topk", "coverage", "mmr", "evidence_coverage"):
                comparison[strategy] = select_evidence(enriched, requirements["facets"], request.budget_chars, strategy, request.top_k, budget=budget)
        selected = comparison[request.selector]
        budget_missing = selected["uncovered"]
        if (state and (budget_missing or not state["payload"]["complete"])) or not selected["payload"]["evidence"]:
            path = "clarify"
            if not selected["payload"]["evidence"]:
                reason += "; no evidence selected"
        return {"path": path, "reason": reason, "parsed": parsed, "state": state,
                "timeline_count": len(timeline), "candidates": candidates, "rejected": rejected,
                "selection": selected, "comparison": comparison, "requirements":requirements, "routing":routing, "retrieval": locals().get("diag", {}),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3), "timings": timings.public_dict(),
                "automatic_approvals": 0, "reader_called": False,
                "limitations": ["source candidates are not approved personal state", "budget covers evidence JSON only; byte/character counts are not reader tokens",
                                "timeline overlapping a range does not prove a fact throughout that range"]}
