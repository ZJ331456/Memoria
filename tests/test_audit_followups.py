"""Isolated audit counterexamples; no production data or model calls."""
import asyncio
import json

import pytest

from memoria.store import Store
from memoria.tools import ToolPolicy, build_registry


@pytest.mark.parametrize('text', [
    '不要记住这条私人信息', '请不要删除任何记忆', '我忘记上次聊了什么，请帮我找找',
    'Do you remember my preference?', 'I remember liking tea.', 'Please do not remember this.',
    "Don't forget my preference.", '请解释“记住我的偏好”是什么意思',
    '请记住红茶，但是不要记住我的偏好', '如果明天有需要，请问我是否添加记忆',
])
def test_write_policy_rejects_negation_recollection_and_quoted_commands(tmp_path, text):
    authorization = ToolPolicy().authorize(text)
    assert not authorization.allowed_write_tools
    store = Store(tmp_path / 'policy.db', 'json')
    try:
        tools = build_registry(store)
        item = store.add_memory('工程临时事实')
        for name, arguments in [('memorize', {'content': '不应保存'}),
                                ('forget_memory', {'memory_id': item['id']})]:
            result = asyncio.run(tools.execute(name, arguments,
                allowed_write_tools=authorization.allowed_write_tools))
            assert not result.ok
        assert len(store.memories()) == 1
    finally:
        store.close()


@pytest.mark.parametrize(('text', 'allowed'), [
    ('请记住我喜欢红茶', {'memorize'}), ('请帮我保存这条信息', {'memorize'}),
    ('请删除这条记忆', {'forget_memory'}), ('忘掉这条记忆', {'forget_memory'}),
    ('Please remember my preference.', {'memorize'}), ('Could you forget this memory?', {'forget_memory'}),
    ('不要删除记忆；请记住我喜欢红茶', {'memorize'}),
])
def test_write_policy_preserves_explicit_positive_commands(text, allowed):
    assert ToolPolicy().authorize(text).allowed_write_tools == allowed


@pytest.mark.parametrize('tool', ['access_claims', 'recall_episodes'])
@pytest.mark.parametrize('mutate', [False, True])
def test_tool_evidence_is_rechecked_after_reader_and_buffered(tmp_path, tool, mutate):
    from tests.test_memory_layers_integration import _app, _without_model_retrieval
    from memoria.claims import ClaimDecision, ClaimDraft, sha
    from memoria.llm import ChatResult
    from memoria.memory.reader import SAFE_UNKNOWN

    async def case():
        app = _app(tmp_path)
        runtime = _without_model_retrieval(app)
        runtime.tool_presentation.enabled = False
        store, claims = app.state.store, app.state.service.claims
        source_session = store.create_session()['id']
        source = store.add_message(source_session, 'user', '工程来源住杭州')
        quote = {'message_id': source['id'], 'start': 0, 'end': len(source['content']),
                 'quote': source['content'], 'content_sha256': sha(source['content'])}
        candidate = claims.create(ClaimDraft(predicate='residence', value='杭州', evidence=[quote]))
        current = claims.approve(candidate['id'], ClaimDecision(expected_revision=1, reason='工程批准'))
        app.state.service.episodes.record(source_session, source['id'], task='工程来源住杭州', result='杭州')
        arguments = {'predicates': ['residence']} if tool == 'access_claims' else {'query': '工程来源住杭州'}
        runtime.tools.get(tool).max_output_chars = 40  # Trusted metadata survives text clipping.
        raw = await runtime.tools.execute(tool, arguments)
        assert raw.ok and raw.memory_dependencies
        runtime.tools.get(tool).max_output_chars = 20000
        calls, events = [], []

        async def reader(messages, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                call = {'id': 'audit-call', 'name': tool, 'arguments': arguments}
                wire = {'id': call['id'], 'type': 'function', 'function': {
                    'name': tool, 'arguments': json.dumps(arguments)}}
                return ChatResult(tool_calls=[call], raw_message={'content': None, 'tool_calls': [wire]})
            assert any(m['role'] == 'tool' and '杭州' in m['content'] for m in messages)
            if mutate:
                if tool == 'access_claims':
                    claims.decide(current['id'], ClaimDecision(expected_revision=current['revision'], reason='工程撤回'), 'retract')
                else:
                    store.delete_session(source_session)
            return ChatResult(content='你住在杭州。', raw_message={'content': '你住在杭州。'})

        async def stream(messages, on_delta=None, **kwargs):
            result = await reader(messages, **kwargs)
            if result.content and on_delta:
                await on_delta(result.content)
            return result

        async def emit(event):
            events.append(event)

        runtime.llm.chat, runtime.llm.chat_stream = reader, stream
        try:
            message, _, trace = await runtime.run(store.create_session()['id'], '继续', emit, skip_memory_enqueue=True)
            assert message['content'] == (SAFE_UNKNOWN if mutate else '你住在杭州。')
            deltas = ''.join(e['content'] for e in events if e['type'] == 'delta')
            assert deltas == message['content']
            assert trace['metadata'].get('memory_freshness') == ('dependency_changed_before_publish' if mutate else None)
        finally:
            store.close()
    asyncio.run(case())


@pytest.mark.parametrize('mutation', ['delete', 'undo', 'edit'])
def test_history_and_online_export_exclude_unavailable_sources(tmp_path, mutation):
    from fastapi.testclient import TestClient
    from tests.test_memory_layers_integration import _app
    app = _app(tmp_path)
    store = app.state.store
    try:
        sid = store.create_session()['id']
        source = store.add_message(sid, 'user', 'AUDIT_SOURCE_SENTINEL')
        item = store.add_memory(source['content'], source='reviewed_conversation',
                                source_ref=source['id'], require_source=True)
        instant = item['created_at']
        if mutation == 'delete':
            store.delete_session(sid)
        elif mutation == 'undo':
            store.undo_memory_sources([source['id']])
        else:
            with store.db:
                store.db.execute('UPDATE messages SET content=? WHERE id=?', ('changed', source['id']))
        assert not store.time_travel(instant)
        client = TestClient(app)
        headers = {'Authorization': 'Bearer admin-secret'}
        for query in ['', 'AUDIT_SOURCE_SENTINEL']:
            response = client.get('/api/memories/time-travel', params={'as_of': instant, 'q': query}, headers=headers)
            assert response.status_code == 200 and response.json() == []
        exported = client.get('/api/memories/export.md', headers=headers)
        assert exported.status_code == 200 and 'AUDIT_SOURCE_SENTINEL' not in exported.text
    finally:
        store.close()


def test_online_export_uses_current_validity_but_history_preserves_valid_versions(tmp_path):
    from datetime import datetime, timedelta, timezone
    from fastapi.testclient import TestClient
    from tests.test_memory_layers_integration import _app
    app = _app(tmp_path)
    store = app.state.store
    try:
        before = store.add_memory('VALID_HISTORICAL_SENTINEL')
        store.correct_memory(before['id'], 'CURRENT_SENTINEL', 'fact', 3, 'engineering')
        instant = datetime.now(timezone.utc)
        future = store.add_memory('FUTURE_SENTINEL')
        expired = store.add_memory('EXPIRED_SENTINEL')
        store.set_temporal(future['id'], (instant + timedelta(days=1)).isoformat())
        store.set_temporal(expired['id'], (instant - timedelta(days=2)).isoformat(), (instant - timedelta(days=1)).isoformat())
        assert before['id'] in {m['id'] for m in store.time_travel(before['created_at'])}
        exported = TestClient(app).get('/api/memories/export.md', headers={'Authorization': 'Bearer admin-secret'})
        assert exported.status_code == 200
        assert 'CURRENT_SENTINEL' in exported.text
        assert all(t not in exported.text for t in ['VALID_HISTORICAL_SENTINEL', 'FUTURE_SENTINEL', 'EXPIRED_SENTINEL'])
    finally:
        store.close()


@pytest.mark.parametrize('mutation', ['correct', 'delete'])
def test_review_reinforcement_target_race_restores_pending_and_allows_retry(tmp_path, mutation):
    from fastapi.testclient import TestClient
    from tests.test_memory_layers_integration import _app
    app = _app(tmp_path)
    store, engine = app.state.store, app.state.service.runtime.memory
    try:
        original = store.add_memory('I prefer green tea without sugar every morning', 'preference')
        content = 'I prefer green tea without sugar every single morning'
        message = store.add_message(store.create_session()['id'], 'user', content)
        job = store.enqueue_memory_job(message['id'], content, 'Engineering reply')
        store.claim_memory_job('audit-worker')
        review = store.stage_memory_reviews(job['id'], 'audit-worker', [
            {'content': content, 'kind': 'preference', 'importance': 3}])[0]
        store.finish_memory_job(job['id'], owner='audit-worker')
        changed = False

        async def decide(*args):
            nonlocal changed
            await asyncio.sleep(0)  # A second writer changes the model's snapshot.
            if not changed:
                if mutation == 'correct':
                    store.correct_memory(original['id'], 'I now prefer black coffee', 'preference', 3, 'engineering')
                else:
                    store.delete_memory(original['id'])
                changed = True
            return {'action': 'reinforce', 'target_id': original['id'], 'reason': 'Engineering decision'}

        engine.decider = decide
        client = TestClient(app)
        headers = {'Authorization': 'Bearer admin-secret'}
        path = '/api/memory-reviews/' + review['id'] + '/approve'
        body = {'content': content, 'kind': 'preference', 'importance': 3}
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 409, response.text
        pending = store.memory_review(review['id'])
        assert pending['status'] == 'pending' and pending['applied_memory_id'] is None
        assert not store.has_memory_operation(message['id'], original['id'])
        retry = client.post(path, json=body, headers=headers)
        assert retry.status_code == 200, retry.text
        approved = retry.json()
        assert approved['status'] == 'approved' and approved['applied_memory_id']
        assert store.memory(approved['applied_memory_id'])['status'] == 'active'
    finally:
        store.close()


@pytest.mark.parametrize('with_default', [False, True])
def test_claim_cache_expires_at_unselected_parent_start_and_selected_parent_end(tmp_path, monkeypatch, with_default):
    import memoria.claims as module
    from memoria.claims import AccessRequest, ClaimDecision, ClaimDraft, ClaimMemory, ViewRequest, sha
    store = Store(tmp_path / 'cache.db', 'json')
    claims = ClaimMemory(store, 'engineering-owner')
    clock = ['2030-01-01T23:59:00.000000+00:00']
    monkeypatch.setattr(module, 'now', lambda: clock[0])
    try:
        sid = store.create_session()['id']

        def approved(value, **kwargs):
            source = store.add_message(sid, 'user', 'Engineering statement: ' + value)
            evidence = {'message_id': source['id'], 'start': 0, 'end': len(source['content']),
                        'quote': source['content'], 'content_sha256': sha(source['content'])}
            candidate = claims.create(ClaimDraft(predicate='response_language', value=value, evidence=[evidence], **kwargs))
            return claims.approve(candidate['id'], ClaimDecision(expected_revision=1, reason='Engineering approval'))

        if with_default:
            approved('中文')
        parent = store.add_memory('Engineering interview preference')
        start, end = '2030-01-02T00:00:00.000000+00:00', '2030-01-02T00:02:00.000000+00:00'
        store.set_temporal(parent['id'], start, end)
        approved('英文', scope={'activity': 'interview'}, memory_id=parent['id'])
        request = AccessRequest(predicates=['response_language'], context={'activity': 'interview'})
        initial = claims.access(request)
        view_id = initial['compiled_view_id'] or claims.compile(ViewRequest(**request.model_dump()))['id']
        assert initial['payload']['next_boundary'] == start
        assert claims.view(view_id)['expires_at'] == start
        clock[0] = start
        assert not claims.view(view_id)['valid']
        current = claims.access(request)
        assert [c['value'] for c in current['payload']['claims']] == ['英文']
        assert current['payload']['next_boundary'] == end
        assert current['path'] == 'retrieve'
        current_view = current['compiled_view_id']
        clock[0] = end
        assert not claims.view(current_view)['valid']
        expired = claims.access(request)
        assert [c['value'] for c in expired['payload']['claims']] == (['中文'] if with_default else [])
    finally:
        store.close()


@pytest.mark.parametrize(('answer', 'units'), [
    ('Alice likes Bob. Bob likes Alice.', ['Alice likes Bob.']),
    ('Alice likes Bob and Bob likes Alice.', ['Alice likes Bob', 'Bob likes Alice']),
    ('-5', ['5']), ('Alice likes Bob. Alice likes Bob or Alice.', ['Alice likes Bob', 'Alice']),
])
def test_support_rejects_uncovered_order_conjunction_or_sign(answer, units):
    from memoria.memory.answer_risk import AnswerRisk
    value = {'verdict': 'supported', 'claims': [
        {'statement': unit, 'relation': 'supported', 'basis': 'explicit',
         'sources': [{'id': 's1', 'quote': 'Alice likes Bob.'}], 'reason': 'Engineering support'} for unit in units],
        'missing_queries': [], 'reason': 'Engineering support'}
    with pytest.raises(ValueError, match='completely covered'):
        AnswerRisk().validate(value, 'Question', {'answer': answer, 'evidence_ids': ['s1']},
                              [{'id': 's1', 'text': 'Alice likes Bob.'}])


@pytest.mark.parametrize('answer', ['Alice likes Bob.\nBob likes Alice.', '“Alice likes Bob.”; Bob likes Alice.'])
def test_support_accepts_all_literal_units_with_sentence_separators(answer):
    from memoria.memory.answer_risk import AnswerRisk
    value = {'verdict': 'supported', 'claims': [
        {'statement': unit, 'relation': 'supported', 'basis': 'explicit',
         'sources': [{'id': 's1', 'quote': unit}], 'reason': 'Engineering support'}
        for unit in ['Alice likes Bob.', 'Bob likes Alice.']],
        'missing_queries': [], 'reason': 'Engineering support'}
    result = AnswerRisk().validate(value, 'Question', {'answer': answer, 'evidence_ids': ['s1']},
                                  [{'id': 's1', 'text': 'Alice likes Bob. Bob likes Alice.'}])
    assert result['accepted'] and not result['semantic_entailment_verified']


@pytest.mark.parametrize('question', [
    'What disease was Alice diagnosed with in May?', 'What disease was Alice diagnosed with in may?',
    'What happened during May?', 'What happened last May?', 'What happened on May 5?',
    'What did May adopt?', 'What condition does May have?', 'May was diagnosed with what disease?',
])
def test_month_or_person_may_does_not_permit_inferred_personal_facts(question):
    from memoria.memory.answer_risk import AnswerRisk, query_risk
    assert not query_risk(question)['allows_background_inference']
    value = {'verdict': 'supported', 'claims': [{'statement': 'anemia', 'relation': 'supported', 'basis': 'inference',
        'sources': [{'id': 's1', 'quote': 'I have fatigue and headaches.'}], 'reason': 'Engineering inference'}],
        'missing_queries': [], 'reason': 'Engineering inference'}
    assert not AnswerRisk().validate(value, question, {'answer': 'anemia', 'evidence_ids': ['s1']},
                                    [{'id': 's1', 'text': 'I have fatigue and headaches.'}])['accepted']


@pytest.mark.parametrize('question', ['What might Alice prefer?', 'What may Alice prefer?',
    'Alice may have which condition?', 'What may she prefer in May?', 'May she prefer tea?',
    'What could May prefer?', '根据原话推测她可能喜欢什么？'])
def test_explicit_speculation_remains_available_even_with_a_month_or_name(question):
    from memoria.memory.answer_risk import query_risk
    assert query_risk(question)['allows_background_inference']
