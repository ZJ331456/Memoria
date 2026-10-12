"""Isolated engineering counterexamples; not users, gold or algorithm scores."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from memoria.claims import AccessRequest, ClaimDecision, ClaimDraft, ClaimError, ClaimMemory, ClaimRevision, ExtractionRequest, sha
from memoria.llm import ChatResult
from memoria.memory.access import MemoryAccessOrchestrator, QueryRequest
from memoria.memory.claim_analysis import AnalysisRequest, analyze_claim
from memoria.store import Store


@pytest.fixture
def core(tmp_path):
    store = Store(tmp_path / 'isolated.db', vector_backend='json')
    claims = ClaimMemory(store, 'engineering-owner')
    sid = store.create_session('Engineering counterexamples')['id']
    yield store, claims, sid
    store.close()


def draft(core, text, value, predicate='residence', **kw):
    store, claims, sid = core
    msg = store.add_message(sid, 'user', text)
    e = {'message_id': msg['id'], 'start': 0, 'end': len(text), 'quote': text, 'content_sha256': sha(text)}
    return claims.create(ClaimDraft(predicate=predicate, value=value, evidence=[e], **kw))


def approved(core, text, value, predicate='residence', **kw):
    c = draft(core, text, value, predicate, **kw)
    return core[1].approve(c['id'], ClaimDecision(expected_revision=c['revision'], reason='Explicit engineering approval'))


def analyze(core, c, llm=None, **kw):
    return asyncio.run(analyze_claim(core[1], llm, c['id'], AnalysisRequest(expected_revision=c['revision'], **kw)))


def test_source_bound_transition_is_only_advice_and_existing_state_unchanged(core):
    old = approved(core, '我住南京', '南京')
    new = draft(core, '2026年10月12日我搬到上海', '上海', fact_start='2026-10-12T00:00:00+08:00', time_precision='day')
    result = analyze(core, new)
    assert result['update']['action'] == 'transition'
    assert result['update']['targets'][0]['revision'] == old['revision']
    assert not result['persisted'] and not result['semantic_support_verified']
    assert result['model_calls'] == 0
    assert core[1].get(old['id'])['fact_end'] is None and core[1].get(new['id'])['status'] == 'pending'
    assert core[1].access(AccessRequest(predicates=['residence']))['payload']['claims'][0]['value'] == '南京'


@pytest.mark.parametrize('kw', [{}, {'fact_start': '2026-10-12T00:00:00+08:00', 'time_precision': 'day'},
                               {'fact_start': '2026-10-12T12:00:00+08:00', 'time_precision': 'exact'}])
def test_unknown_or_invented_event_time_never_recommends_transition(core, kw):
    approved(core, '我住南京', '南京')
    new = draft(core, '我已经搬到上海', '上海', **kw)
    assert analyze(core, new)['update']['action'] == 'review'


def test_date_alone_does_not_support_exact_instant(core):
    approved(core, '我住南京', '南京')
    new = draft(core, '2026年10月12日我搬到上海', '上海', fact_start='2026-10-12T15:00:00+08:00', time_precision='exact')
    assert analyze(core, new)['update']['action'] == 'review'


def test_ascii_values_allow_chinese_boundaries_but_not_embedded_english(core):
    okay = draft(core, '我喜欢Rust', 'Rust', 'hobby')
    assert analyze(core, okay)['update']['action'] == 'create'
    wrong = draft(core, 'I trust this project', 'Rust', 'hobby')
    assert analyze(core, wrong)['update']['action'] == 'review'


def test_source_iso_instant_supports_exact_boundary(core):
    approved(core, '我住南京', '南京')
    new = draft(core, '2026-10-12T15:00:00+08:00我搬到上海', '上海', fact_start='2026-10-12T15:00:00+08:00', time_precision='exact')
    assert analyze(core, new)['update']['action'] == 'transition'


def test_date_and_change_in_another_clause_do_not_ground_this_value(core):
    approved(core, '我住南京', '南京')
    new = draft(core, '2026年10月12日我搬到北京。现在我住上海', '上海',
                fact_start='2026-10-12T00:00:00+08:00', time_precision='day')
    result = analyze(core, new)
    assert result['update']['action'] == 'review'
    assert not result['evidence_verification']['change_cue_present']
    assert not result['evidence_verification']['event_times_bound']['fact_start']


def test_additive_hobby_and_conditional_exception_preserve_other_claims(core):
    old = approved(core, '我喜欢Python', 'Python', 'hobby')
    new = draft(core, '我也喜欢Rust', 'Rust', 'hobby')
    assert analyze(core, new)['update']['action'] == 'create'
    assert core[1].get(old['id'])['status'] == 'approved'
    language = approved(core, '默认用中文', '中文', 'response_language')
    special = draft(core, '面试模拟时用英文', '英文', 'response_language', scope={'activity': 'interview'})
    result = analyze(core, special)
    assert result['update']['action'] == 'create'
    assert result['update']['relations'][0]['relation'] == 'conditional_exception'
    assert core[1].get(language['id'])['fact_end'] is None


@pytest.mark.parametrize('text,value,kw,reason', [
    ('我朋友喜欢咖啡', '咖啡', {}, 'subject_ambiguous'),
    ('我打算住上海', '上海', {}, 'possible_plan'),
    ('面试模拟时用英文', '英文', {}, 'condition_dropped'),
    ('我不喜欢咖啡', '咖啡', {}, 'negation'),
    ('我喜欢咖啡', '茶', {}, 'value_not_literal'),
])
def test_lexical_risk_cannot_be_waived_by_model_supported(core, text, value, kw, reason):
    c = draft(core, text, value, **kw)
    async def supported(*args, **kwargs):
        return ChatResult(content='{"assessment":"Supported"}')
    result = analyze(core, c, SimpleNamespace(chat=supported), use_model=True)
    assert result['assessment'] == 'Supported' and result['update']['action'] == 'review'
    assert result['model_calls'] == 1
    assert any(reason in flag for flag in result['evidence_verification']['blockers'])


def test_plan_completion_and_polarity_conflict_require_review(core):
    approved(core, '我计划学习Rust', 'Rust', 'learning_goal', modality='plan')
    complete = draft(core, '我已经学习Rust', 'Rust', 'learning_goal')
    assert analyze(core, complete)['update']['relations'][0]['relation'] == 'plan_resolution'
    old = approved(core, '我喜欢咖啡', '咖啡', 'hobby')
    neg = draft(core, '我不喜欢咖啡', '咖啡', 'hobby', polarity='negative')
    result = analyze(core, neg)
    assert result['update']['action'] == 'review' and result['update']['targets'][0]['id'] == old['id']


def test_model_target_and_quote_are_checked_without_writes(core):
    old = approved(core, '我住南京', '南京')
    new = draft(core, '我住上海', '上海')
    before = core[1].history(old['id'])
    async def invented(*args, **kwargs):
        return ChatResult(content=json.dumps({'relations': [{'target_id': 'invented', 'relation': 'state_change', 'evidence_quote': '我住上海'}]}))
    with pytest.raises(ClaimError, match='目标或原话无效'):
        analyze(core, new, SimpleNamespace(chat=invented), use_relation_model=True)
    async def invalid_quote(*args, **kwargs):
        return ChatResult(content=json.dumps({'relations': [{'target_id': old['id'], 'relation': 'state_change', 'evidence_quote': 'I fabricate a date'}]}))
    with pytest.raises(ClaimError, match='目标或原话无效'):
        analyze(core, new, SimpleNamespace(chat=invalid_quote), use_relation_model=True)
    assert core[1].history(old['id']) == before


def test_model_cannot_invent_duplicate_or_bypass_missing_change_time(core):
    old = approved(core, '我住南京', '南京')
    new = draft(core, '我住上海', '上海')
    for relation in ('duplicate', 'coexisting_value', 'state_change'):
        async def proposed(*args, **kwargs):
            return ChatResult(content=json.dumps({'relations': [{'target_id': old['id'], 'relation': relation, 'evidence_quote': '我住上海'}]}), usage={'total_tokens': 13})
        result = analyze(core, new, SimpleNamespace(chat=proposed), use_relation_model=True)
        assert result['update']['action'] == 'review'
        assert result['relation_usage']['total_tokens'] == 13


def test_peer_revision_or_source_change_invalidates_awaited_advice(core):
    old = approved(core, '我住南京', '南京')
    new = draft(core, '我住上海', '上海')
    async def changed(*args, **kwargs):
        core[1].decide(old['id'], ClaimDecision(expected_revision=old['revision'], reason='engineering revoke'), 'retract')
        return ChatResult(content='{"assessment":"Supported"}')
    with pytest.raises(ClaimError, match='关系目标变化'):
        analyze(core, new, SimpleNamespace(chat=changed), use_model=True)


@pytest.mark.parametrize('change', ['source', 'new_peer'])
def test_related_source_or_pool_change_invalidates_advice(core, change):
    old = approved(core, '我喜欢Python', 'Python', 'hobby')
    new = draft(core, '我喜欢Rust', 'Rust', 'hobby')
    async def changed(*args, **kwargs):
        if change == 'source':
            with core[0].db:
                core[0].db.execute('UPDATE messages SET content=? WHERE id=?', ('source changed', old['evidence'][0]['message_id']))
        else:
            approved(core, '我喜欢C++', 'C++', 'hobby')
        return ChatResult(content='{"assessment":"Supported"}')
    with pytest.raises(ClaimError, match='关系目标变化'):
        analyze(core, new, SimpleNamespace(chat=changed), use_model=True)


def test_unrelated_predicate_does_not_invalidate_advice(core):
    new = draft(core, '我喜欢Rust', 'Rust', 'hobby')
    async def changed(*args, **kwargs):
        approved(core, '我住南京', '南京')
        return ChatResult(content='{"assessment":"Supported"}')
    assert analyze(core, new, SimpleNamespace(chat=changed), use_model=True)['update']['action'] == 'create'


def test_truncated_pool_and_pending_target_are_never_actionable(core):
    for value in ('Python', 'Rust'):
        approved(core, '我喜欢' + value, value, 'hobby')
    new = draft(core, '我喜欢C++', 'C++', 'hobby')
    assert analyze(core, new, peer_limit=1)['update']['reason'] == 'peer_pool_truncated'
    old = approved(core, '我住南京', '南京')
    revised = draft(core, '我住上海', '上海')
    core[1].revise(old['id'], ClaimRevision(expected_revision=old['revision'], action='correct', value='上海', evidence=revised['evidence']))
    assert analyze(core, revised)['update']['reason'] == 'target_has_pending_revision'


def test_extraction_emits_shadow_proposals_without_extra_model_calls(core):
    old = approved(core, '我喜欢Python', 'Python', 'hobby')
    msg = core[0].add_message(core[2], 'user', '我也喜欢Rust')
    calls = []
    async def extracted(*args, **kwargs):
        calls.append(1)
        return ChatResult(content='{"claims":[{"predicate":"hobby","value":"Rust","evidence_quote":"我也喜欢Rust"}]}')
    result = asyncio.run(core[1].extract(SimpleNamespace(chat=extracted), ExtractionRequest(message_id=msg['id'])))
    assert len(calls) == 1 and result['shadow_model_calls'] == 0
    assert result['shadow_updates'][0]['update']['action'] == 'create'
    assert core[1].get(old['id'])['status'] == 'approved'


def test_explicit_default_specific_and_unspecified_queries_are_distinct(core):
    approved(core, '默认用中文', '中文', 'response_language')
    approved(core, '面试模拟时用英文', '英文', 'response_language', scope={'activity': 'interview'})
    engine = SimpleNamespace(retrieve=None)  # Claim-only path must not need a retriever.
    access = MemoryAccessOrchestrator(core[1], engine)
    normal = asyncio.run(access.access(QueryRequest(query='我平时用什么语言', mode='claim')))
    special = asyncio.run(access.access(QueryRequest(query='我模拟面试时用什么语言', mode='claim')))
    absent = asyncio.run(access.access(QueryRequest(query='我用什么语言', mode='claim')))
    assert normal['path'] == 'retrieve' and normal['state']['payload']['claims'][0]['value'] == '中文'
    assert special['path'] == 'retrieve' and special['state']['payload']['claims'][0]['value'] == '英文'
    assert absent['path'] == 'clarify'
    assert asyncio.run(access.access(QueryRequest(query='我平时用什么语言', mode='claim')))['path'] == 'view'
    with pytest.raises(ValidationError):
        AccessRequest(predicates=['response_language'], scope_mode='default', context={'activity': 'interview'})


@pytest.mark.parametrize('body', [
    {'query': '我平时用什么语言', 'context': {'activity': 'interview'}},
    {'query': '我平时模拟面试时用什么语言'},
    {'query': '我平时用什么语言', 'scope_mode': 'applicable'},
])
def test_exact_time_cannot_override_scope_conflict(core, body):
    result = asyncio.run(MemoryAccessOrchestrator(core[1], None).access(QueryRequest(**body, fact_as_of='2026-10-12T00:00:00Z')))
    assert result['path'] == 'clarify'
