from tests.kernel_factory import tool_kernel
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
        tools = build_registry(tool_kernel(store))
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


@pytest.mark.parametrize('tight_budget', [False, True])
def test_full_history_repair_never_drops_query_or_evidence(tmp_path, monkeypatch, tight_budget):
    from dataclasses import replace
    from memoria.config import Settings
    from memoria.experiments import locomo_eval as bench
    from memoria.experiments.history import HistoryWorkspace
    from memoria.experiments.roles import load_roles
    from memoria.llm import ChatResult
    from memoria.prompting.reader_budget import ReaderBudget
    from tests.test_locomo_eval import fixture_records

    source = tmp_path / 'source.json'
    source.write_text(json.dumps(fixture_records()), encoding='utf-8')
    monkeypatch.setattr(bench, 'SOURCE_SHA256', bench.digest_file(source))
    config = tmp_path / 'config.toml'
    config.write_text('[storage]\ndatabase="'+(tmp_path/'data/memoria.db').as_posix()+'"\n[llm.main]\nmodel="temporary"\nbase_url="http://localhost:11434/v1"\n', encoding='utf-8')
    settings, roles = Settings.load(config), load_roles(None)
    cases, histories, _ = bench.benchmark_cases(fixture_records(), 'smoke')
    packet = {'question': cases[0]['question'], 'evidence': [
        {k: m[k] for k in ('id', 'speaker', 'session_date_raw', 'text')} for m in histories[cases[0]['conversation_id']]],
        'instruction': 'Recheck against the complete original conversation. Answer only if supported; otherwise Not mentioned.'}
    messages = [{'role': 'system', 'content': bench.SYSTEM}, {'role': 'user', 'content': bench.canonical(packet)}]
    initial = asyncio.run(ReaderBudget(settings).measure(settings.main, messages, None, roles['reader'].max_tokens,
                                                       temperature=0, top_p=1))
    if tight_budget:
        settings = replace(settings, reader_input_bytes=initial['input_bytes'] + 1)

    class Retriever:
        document_encodes = 0
        query_encodes = 0
        def __init__(self, *args, **kwargs): pass
        async def select(self, history, *args, **kwargs):
            self.query_encodes += 1
            return history, {}

    repairs = []
    class Reader:
        def __init__(self, settings, roles, recorder, timings):
            self.calls, self.recorder = [], recorder
        def public_roles(self): return {'reader': {'model': 'engineering-only'}}
        async def call(self, role, messages, **kwargs):
            packet = json.loads(messages[1]['content'])
            assert packet['question'] == cases[0]['question'] and packet['evidence']
            assert 'GOLD' not in json.dumps(messages)
            self.calls.append({'usage': {'total_tokens': 1}})
            self.recorder.append('calls.jsonl', {'call': len(self.calls) - 1, 'usage': {'total_tokens': 1}})
            if len(self.calls) == 1:
                output = {'status': 'abstain', 'answer': 'Not mentioned.', 'evidence_ids': []}
            elif len(self.calls) == 2:
                output = {'status': 'answer', 'answer': 'temporary', 'evidence_ids': ['unseen']}
            else:
                repairs.append(packet)
                output = {'status': 'answer', 'answer': 'temporary', 'evidence_ids': [packet['evidence'][0]['id']]}
            return ChatResult(content=json.dumps(output), usage={'total_tokens': 1})

    monkeypatch.setattr(bench, 'PrefixRetriever', Retriever)
    path = asyncio.run(bench.execute(HistoryWorkspace(tmp_path / 'data/users' / ('a'*32)), settings, source, scope='smoke',
        client_factory=Reader, embedding_factory=lambda cfg: None, roles=roles))
    rows = [json.loads(line) for line in (path / 'cases.jsonl').read_text().splitlines()]
    assert len(rows) == 10
    if tight_budget:
        assert not repairs
        assert all(r['fallback_status'] == 'error' and r['fallback_error'] == 'ContextLengthError' for r in rows)
        assert all(r['answer_status'] == 'abstain' for r in rows)
    else:
        assert len(repairs) == 10
        assert all(r['fallback_status'] == 'success' and r['fallback_reader_budget']['dropped_messages'] == 0 for r in rows)
        assert all(len(r['presented_ids']) == 1 and r['answer_status'] == 'answer' for r in rows)


@pytest.mark.parametrize('action', ['cancel', 'delete'])
def test_stream_reserves_session_before_start_and_cancel_always_releases_it(tmp_path, action):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from memoria.api.app import ChatBody
    from tests.test_memory_layers_integration import _app

    async def case():
        app = _app(tmp_path)
        store = app.state.store
        sid = store.create_session()['id']
        def route_leaves(router):
            # FastAPI includes APIRouter lazily; the cancellation assertions stay unchanged.
            for route in router.routes:
                nested = getattr(route, 'original_router', None)
                if nested is None:
                    yield route
                else:
                    yield from route_leaves(nested)
        routes = list(route_leaves(app))
        stream = next(r.endpoint for r in routes if getattr(r, 'path', '') == '/api/sessions/{session_id}/chat/stream')
        cancel = next(r.endpoint for r in routes if getattr(r, 'path', '') == '/api/sessions/{session_id}/cancel')
        delete = next(r.endpoint for r in routes if getattr(r, 'path', '') == '/api/sessions/{session_id}' and 'DELETE' in r.methods)
        calls = []
        async def chat(session_id, content, *args, **kwargs):
            calls.append(content)
            return store.add_message(session_id, 'assistant', content), [], {}
        async def disconnected(): return False
        request = SimpleNamespace(state=SimpleNamespace(request_id='audit'), is_disconnected=disconnected)
        app.state.service.chat_with_trace = chat
        try:
            # The stream task is reserved but has not had a chance to take the lock.
            response = await stream(sid, ChatBody(content='stream must not run'), request)
            assert app.state.service._busy_check()
            with pytest.raises(HTTPException) as error:
                await app.state.chat_turn(sid, 'duplicate must not run')
            assert error.value.status_code == 409
            if action == 'cancel':
                assert (await cancel(sid))['status'] == 'cancelled'
            else:
                await delete(sid)
            async def collect():
                return [chunk async for chunk in response.body_iterator]
            chunks = await asyncio.wait_for(collect(), timeout=2)
            assert 'cancelled' in ''.join(chunks)
            assert not calls and not app.state.service._busy_check()
            target = sid if action == 'cancel' else store.create_session()['id']
            result = await app.state.chat_turn(target, 'next valid turn')
            assert result['message']['content'] == 'next valid turn'
            assert calls == ['next valid turn'] and not app.state.service._busy_check()
        finally:
            store.close()
    asyncio.run(case())


@pytest.mark.parametrize('status', ['abstain', 'clarify'])
@pytest.mark.parametrize('citations', [[], ['s1']])
def test_benchmark_nonanswer_cannot_publish_or_score_embedded_facts(status, citations):
    from memoria.experiments.locomo import parse_answer, UNKNOWN_ANSWER
    from memoria.experiments.locomo_eval import score
    from memoria.llm import ChatResult
    raw = json.dumps({'status': status, 'answer': 'three dogs', 'evidence_ids': citations})
    result = ChatResult(content=raw)
    parsed = parse_answer(result, ['s1'])
    assert result.content == raw  # Provider record is never rewritten.
    assert parsed == {'status': status, 'answer': UNKNOWN_ANSWER, 'evidence_ids': []}
    rows = [{'id': 'case', 'status': 'success', 'answer_status': status,
             'answer': parsed['answer'], 'evidence_ids': parsed['evidence_ids']}]
    gold = [{'id': 'case', 'category': 4, 'answer': 'three dogs',
             'evidence_ids': ['s1'], 'unresolved_evidence_ids': []}]
    assert score(rows, gold)['ordinary_answer_f1'] == 0
    answered = parse_answer(ChatResult(content=json.dumps({
        'status': 'answer', 'answer': 'three dogs', 'evidence_ids': ['s1']})), ['s1'])
    rows[0].update(answer=answered['answer'], answer_status='answer', evidence_ids=['s1'])
    assert score(rows, gold)['ordinary_answer_f1'] == 1


def test_nonanswer_still_rejects_forged_citations_before_fixed_refusal():
    from memoria.experiments.locomo import parse_answer
    from memoria.llm import ChatResult
    with pytest.raises(ValueError):
        parse_answer(ChatResult(content=json.dumps({
            'status': 'abstain', 'answer': 'three dogs', 'evidence_ids': ['foreign']})), ['s1'])
