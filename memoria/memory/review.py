"""An explicit, source-bound approval atomically publishes narrative and typed state."""
from ..claims import ClaimDraft, ClaimDecision, ClaimError
from ..store import now
from .evidence_verifier import EvidenceVerifier


async def approve_review_with_claim(service, review, body):
    store, claims, engine = service.store, service.claims, service.runtime.memory
    draft = body.claim
    if draft.memory_id is not None or draft.proposal_origin != 'manual':
        raise ClaimError(422, '联动审核只接受人工核对的字段，关联记忆由服务器生成')
    if any(e.message_id != review['source_ref'] for e in draft.evidence+draft.condition_evidence):
        raise ClaimError(422, '联动Claim必须引用此候选的同一原话')
    vector=chunks=None
    namespace = engine.store.embedding_namespace
    if engine.embedder and engine.embedder.enabled:
        try:
            vector,chunks = await engine._embed_document(body.content)
        except Exception:
            pass  # Approved lexical storage remains available; indexing is recoverable.
    if namespace != engine.store.embedding_namespace:
        vector=chunks=None
    with store._memory_transaction():
        current = store.memory_review(review['id'])
        if not current or current['status']!='applying':
            raise ClaimError(409, '审核状态已变化')
        store._require_memory_source(review['source_ref'])
        action,memory = store.add_memory_if_absent(body.content,body.kind,body.importance,'reviewed_conversation',vector,
            source_ref=review['source_ref'],require_source=True,embedding_namespace=namespace,embedding_chunks=chunks)
        candidate = claims.create(ClaimDraft(**{**draft.model_dump(),'memory_id':memory['id']}))
        verification = EvidenceVerifier().verify(claims,candidate)
        if verification['blockers']:
            raise ClaimError(422, '请先解决证据/主体/条件/时间约束：'+';'.join(verification['blockers']))
        approved = claims.approve(candidate['id'],ClaimDecision(expected_revision=candidate['revision'],reason=draft.reason))
        store.db.execute("UPDATE memory_reviews SET status='approved',applied_memory_id=?,applied_action=?,applied_claim_id=?,updated_at=? WHERE id=?",
            (memory['id'],action,approved['id'],now(),review['id']))
    engine.refresh_markdown()
    return store.memory_review(review['id'])
