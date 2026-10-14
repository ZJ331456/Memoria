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
