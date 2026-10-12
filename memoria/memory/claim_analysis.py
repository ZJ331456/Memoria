"""Read-only evolution proposals: verify, relate, gate, recheck; never commit."""
from __future__ import annotations

from pydantic import Field

from ..claims import ClaimError, ClaimMemory, Strict, canonical
from .claim_relations import ClaimRelationResolver
from .evidence_verifier import EvidenceVerifier


class AnalysisRequest(Strict):
    expected_revision: int = Field(ge=1, strict=True)
    use_model: bool = Field(default=False, strict=True)
    use_relation_model: bool = Field(default=False, strict=True)
    peer_limit: int = Field(default=32, ge=1, le=64, strict=True)


def update_suggestion(memory: ClaimMemory, item: dict) -> dict:
    if not item.get("source_available"):
        return {"action": "review", "reason": "source_unavailable", "targets": [], "automatic_mutations": 0}
    resolver = ClaimRelationResolver()
    return resolver.resolve(memory, item, resolver.snapshot(memory, item))


async def analyze_claim(memory: ClaimMemory, llm, cid: str, request: AnalysisRequest) -> dict:
    resolver, verifier = ClaimRelationResolver(), EvidenceVerifier()
    with memory.store._memory_transaction():
        memory._expected(cid, request.expected_revision)
        item = memory.get(cid)
        if not item["source_available"]:
            raise ClaimError(409, "来源不可用，不能评估支持度")
        verification = verifier.verify(memory, item)
        snapshot = resolver.snapshot(memory, item, request.peer_limit)
        fingerprint = canonical({"candidate": item, "peers": snapshot})
    assessment, usage, relation_usage, relations, model = "not_attempted", None, None, {}, None
    if request.use_model:
        assessment, usage = await verifier.assess(llm, item)
    if request.use_relation_model:
        relations, relation_usage = await resolver.assess(llm, item, snapshot)
    model_calls = int(request.use_model) + int(request.use_relation_model and bool(snapshot["peers"]))
    if model_calls:
        model = llm.settings.main.model if getattr(llm, "settings", None) else None
    with memory.store._memory_transaction():
        memory._expected(cid, request.expected_revision)
        current = memory.get(cid)
        current_snapshot = resolver.snapshot(memory, current, request.peer_limit)
        if canonical({"candidate": current, "peers": current_snapshot}) != fingerprint:
            raise ClaimError(409, "调用期间来源、候选或关系目标变化，评估结果作废")
        suggestion = resolver.resolve(memory, current, current_snapshot, verification, relations, assessment)
    return {"claim_id": cid, "revision": item["revision"], "source_span_valid": True,
            "normalized_value_suggestion": item["value"].strip(), "scope": item["scope"], "update": suggestion,
            "evidence_verification": verification, "assessment": assessment,
            "assessment_origin": "model_advisory" if request.use_model else "not_attempted",
            "semantic_support_verified": False, "automatic_approvals": 0, "persisted": False,
            "mode": "shadow", "usage": usage, "relation_usage": relation_usage,
            "model": model, "model_revision": None, "model_calls": model_calls}
