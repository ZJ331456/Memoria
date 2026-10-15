"""An explicit, source-bound approval atomically publishes narrative and typed state."""
from ..claims import ClaimDraft, ClaimDecision, ClaimError, canonical, sha
from ..store import now
from .evidence_verifier import EvidenceVerifier


async def approve_review_with_claim(service, review, body):
    store, claims, engine = service.store, service.claims, service.runtime.memory
    draft = body.claim
    if draft.memory_id is not None or draft.proposal_origin != 'manual':
        raise ClaimError(422, '联动审核只接受人工核对的字段，关联记忆由服务器生成')
    if any(e.message_id != review['source_ref'] for e in draft.evidence+draft.condition_evidence):
        raise ClaimError(422, '联动Claim必须引用此候选的同一原话')
    from .auto_review import ALIAS_BLOCKER
    verifier=EvidenceVerifier()
    preview={**draft.model_dump(),'source_available':True}
    rules=verifier.verify(claims,preview)
    hard=[b for b in rules['blockers'] if b!=ALIAS_BLOCKER]
    if hard:
        raise ClaimError(422,'主体、条件、否定或时间仍有疑问，请修订结构化字段；普通记忆可取消联动后单独保存：'+';'.join(hard))
    semantic=None
    if ALIAS_BLOCKER in rules['blockers']:
        if not service.settings.main.is_ready():
            raise ClaimError(422,'该值是改写或别名，需要LLM语义核验；当前模型未就绪。可取消结构化联动，仅保存普通记忆。')
        semantic,usage=await verifier.assess(service.llm,preview)
        if semantic!='Supported':
            raise ClaimError(422,'LLM无法确认结构化属性和值受到原话支持，请核对字段；也可取消联动，仅保存普通记忆。')
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
        if any(b!=ALIAS_BLOCKER or semantic!='Supported' for b in verification['blockers']):
            raise ClaimError(422, '请先解决证据/主体/条件/时间约束：'+';'.join(verification['blockers']))
        approved = claims.approve(candidate['id'],ClaimDecision(expected_revision=candidate['revision'],reason=draft.reason))
        store.db.execute("UPDATE memory_reviews SET status='approved',applied_memory_id=?,applied_action=?,applied_claim_id=?,updated_at=? WHERE id=?",
            (memory['id'],action,approved['id'],now(),review['id']))
        if semantic:
            key=sha(canonical({'draft':draft.model_dump(),'content':body.content}))
            store.record_review_assessment(review['id'],key,{'policy':'linked_claim_semantic_v1','input_sha256':key,'decision':'accept',
                'reviewer':'llm_with_human_confirmation','assessment':semantic,'model':service.settings.main.model,'model_called':True,
                'usage':usage,'reason_codes':['semantic_paraphrase_supported']})
    engine.refresh_markdown()
    return store.memory_review(review['id'])
