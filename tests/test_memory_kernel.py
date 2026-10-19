"""Temporary engineering inputs only; no personal history, paid API or benchmark claims."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from memoria.api import create_app
from memoria.api.app import _create_tenant_app
from memoria.config import Settings
from memoria.llm import LLMClient
from memoria.memory.budget import TurnMemoryBudget
from memoria.memory.kernel import MemoryKernel
from memoria.memory.library import FeedbackInput, LibraryError, RevisionInput, SourceInput
from memoria.memory.records import MemorySearch
from memoria.observability.context import RequestContext
from memoria.provenance import message_ingress
from memoria.store import Store


def config_at(directory):
    directory.mkdir(parents=True, exist_ok=True)
    config = directory / 'engineering.toml'
    config.write_text('[storage]\ndatabase="' + (directory / 'test.db').as_posix() + '"\n'
        '[memory.markdown]\nenabled=false\n[llm.main]\nmodel="engineering-only"\n'
        'base_url="https://model.invalid/v1"\napi_key="engineering-only"\n', encoding='utf-8')
    return config


@pytest.fixture
def core(tmp_path):
    settings = Settings.load(config_at(tmp_path))
    store = Store(settings.database, 'json')
    llm = LLMClient(settings)
    llm.chat = AsyncMock(side_effect=AssertionError('Retrieval must not run an LLM'))
    kernel = MemoryKernel(settings, store, llm)
    yield kernel
    store.close()


def source(core, **fields):
    return core.ingest(SourceInput(**({'kind': 'document', 'title': '工程来源',
        'content': '工程检索需要保留原始来源和配置校验。', 'authorship': 'external'} | fields)))


def search(core, **kwargs):
    return asyncio.run(core.search(MemorySearch(query='工程检索', memory_mode='bm25', **kwargs)))


@pytest.mark.parametrize('kind', ['document', 'note', 'task_result'])
def test_headless_native_ingestion_does_not_masquerade_as_personal_facts(core, kind):
    item = source(core, kind=kind)
    result = search(core, domains=['library'])
    assert result['evidence'][0]['source_id'] == item['id']
    assert result['evidence'][0]['provenance']['authorship'] == 'external'
    assert result['evidence'][0]['representation'] == 'source_quote'
    assert core.store.sessions() == [] and core.store.memories() == []
    for table in ('messages', 'memory_jobs', 'memory_reviews', 'memory_observations', 'claim_roots'):
        assert core.store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
    assert not hasattr(core, 'runtime') and not hasattr(core, 'tools')
    core.llm.chat.assert_not_called()


def test_conversation_adapter_keeps_roles_channels_and_unknown_fact_time(core):
    sid = core.store.create_session('Not an authorship proof')['id']
    with message_ingress('telegram'):
        message = core.record_message(sid, 'assistant', '工程检索的系统回答，不是用户自述。')
    hit = search(core, domains=['conversation'])['evidence'][0]
    assert hit['provenance']['role'] == 'assistant'
    assert hit['provenance']['origin'] == {'channel': 'telegram', 'basis': 'server_ingress'}
    assert hit['provenance']['fact_time'] is None
    full = core.read('conversation', message['id'])
    assert full['evidence']['content'] == message['content']
    assert core.dependency_valid(full['dependency'])
    core.store.undo_memory_sources([message['id']])
    assert search(core, domains=['conversation'])['evidence'] == []
    assert not core.dependency_valid(full['dependency'])
    with pytest.raises(LibraryError):
        core.read('conversation', message['id'])


def test_public_dialogue_can_be_read_without_becoming_personal_evidence(core):
    sid = core.store.create_session('Engineering corpus')['id']
    message = core.record_message(sid, 'user', '工程检索公开对话样本。')
    with core.store._memory_transaction():
        receipt = json.dumps({'source_mapping': [{'message_ids': [message['id']]}]})
        core.store.db.execute('INSERT INTO dataset_imports VALUES(?,?,?,?,?)',
            ('engineering-corpus', 'engineering-sha', '{}', receipt, message['created_at']))
    hit = search(core, domains=['conversation'])['evidence'][0]
    assert hit['source_id'] == message['id']
    assert hit['provenance']['personal_evidence_eligible'] is False
    assert core.store.memories() == []


def test_source_rank_selection_preserves_all_domains_without_comparing_raw_scores(core):
    source(core)
    sid = core.store.create_session()['id']
    core.record_message(sid, 'user', '工程检索测试原话。')
    memory = core.store.add_memory('工程检索的工程归纳。')
    result = search(core, domains=['memory', 'conversation', 'library'], limit=3)
    assert [e['domain'] for e in result['evidence']] == ['memory', 'conversation', 'library']
    assert [e['representation'] for e in result['evidence']] == ['derived_memory', 'message_quote', 'source_quote']
    assert core.read('memory', memory['id'])['evidence']['representation'] == 'derived_memory'
    assert all(core.dependency_valid(d) for d in result['dependencies'])
    assert 'embedding' not in json.dumps(result)
    core.llm.chat.assert_not_called()


def test_llm_is_optional_for_storage_search_and_source_reads(tmp_path):
    settings = Settings.load(config_at(tmp_path))
    store = Store(settings.database, 'json')
    try:
        core = MemoryKernel(settings, store)
        item = source(core)
        assert search(core, domains=['library'])['evidence']
        assert core.read('library', item['id'])['evidence']['content'] == item['revision']['content']
        assert core.formation is None and core.auto_review is None and core.memory_worker is None
        with pytest.raises(LibraryError, match='未配置LLM'):
            asyncio.run(core.library.distill(item['id']))
        assert store.db.execute('SELECT count(*) FROM library_calls').fetchone()[0] == 0
    finally:
        store.close()


def test_long_matched_memory_chunk_keeps_query_tail_in_an_exact_excerpt(core):
    from memoria.memory.records import SourceReaders
    item = core.store.add_memory('prefix ' * 230 + '工程检索 tail')
    item['retrieval'] = {'chunk': {'start': 0, 'end': len(item['content'])}}
    record = SourceReaders(core.store, core.library).memory(item, '工程检索')
    assert '工程检索' in record.content
    assert item['content'][record.start:record.end] == record.content


@pytest.mark.parametrize('budget', [800, 1600, 3500, 16000])
def test_complete_budget_counts_json_provenance_and_dependencies_without_cutting_quotes(core, budget):
    source(core, title='多语言标题' * 25, content=('前言。' * 260 + '工程检索保留全部来源。') * 2)
    core.store.add_memory('工程检索的工程摘要。' * 80)
    result = search(core, domains=['library', 'memory'], budget_chars=budget)
    assert result['budget']['used'] == len(json.dumps(result, ensure_ascii=False)) <= budget
    assert result['selection']['selected_count'] == len(result['evidence'])
    for hit in result['evidence']:
        raw = core.read(hit['domain'], hit['source_id'])['evidence']['content']
        assert raw[hit['start']:hit['end']] == hit['content']


def test_multi_chunk_library_evidence_retains_distinct_spans_and_one_dependency(core):
    item = source(core, content='工程检索第一部分。' + '前半内容。' * 160 + '工程检索第二部分。' + '后半内容。' * 150)
    result = search(core, domains=['library'], limit=4)
    hits = result['evidence']
    assert len(hits) == 2 and len(result['dependencies']) == 1
    assert hits[0]['start'] != hits[1]['start']
    assert all(item['revision']['content'][h['start']:h['end']] == h['content'] for h in hits)


def test_working_scope_is_bound_to_an_owned_session_for_search_read_and_dependency(core):
    a, b = core.store.create_session()['id'], core.store.create_session()['id']
    item = source(core, scope='working', session_id=a)
    assert search(core, domains=['library'])['evidence'] == []
    assert search(core, domains=['library'], session_id=b)['evidence'] == []
    hit = search(core, domains=['library'], session_id=a)
    dep = hit['dependencies'][0]
    assert not core.dependency_valid(dep) and not core.dependency_valid(dep, session_id=b)
    assert core.dependency_valid(dep, session_id=a)
    for sid in (None, b, 'unowned-session'):
        with pytest.raises(LibraryError):
            core.read('library', item['id'], session_id=sid)
    assert core.read('library', item['id'], session_id=a)['dependency'] == dep


def test_revisions_feedback_and_withdrawal_invalidate_the_same_read_contract(core):
    item = source(core, kind='task_result')
    dep = search(core, domains=['library'])['dependencies'][0]
    core.library.feedback(item['id'], FeedbackInput(expected_revision_id=item['revision']['id'],
        outcome='unknown', content='工程输入，仅记录反馈，未核验效果。'))
    assert not core.dependency_valid(dep)
    dep = search(core, domains=['library'])['dependencies'][0]
    updated = core.library.revise(item['id'], RevisionInput(expected_revision_id=item['revision']['id'],
        title='新版本', content='新版工程检索资料。'))
    assert not core.dependency_valid(dep)
    current = core.read('library', item['id'])
    assert current['evidence']['provenance']['revision_id'] == updated['revision']['id']
    assert core.library.version(item['id'], item['revision']['id'])['is_current'] is False
    core.library.retract(item['id'])
    assert not core.dependency_valid(current['dependency'])
    assert search(core, domains=['library'])['evidence'] == []


def test_mutation_between_candidates_and_selection_cannot_publish_stale_library(core, monkeypatch):
    item = source(core)
    original = core.library.search
    def race(*args, **kwargs):
        rows = original(*args, **kwargs)
        core.library.retract(item['id'])
        return rows
    monkeypatch.setattr(core.library, 'search', race)
    result = search(core, domains=['library'])
    assert result['evidence'] == [] and result['dependencies'] == []
    assert result['selection']['skipped_stale'] == 1


@pytest.mark.parametrize('fields', [{'domains': []}, {'domains': ['library', 'library']},
    {'user_id': 'forged'}, {'query': '   '}, {'budget_chars': True}, {'limit': 1.5}])
def test_contract_does_not_accept_caller_identity_or_ambiguous_inputs(fields):
    with pytest.raises(ValidationError):
        MemorySearch.model_validate({'query': '工程检索'} | fields)


def test_runtime_uses_kernel_and_both_tools_share_atomic_budget(tmp_path):
    app = _create_tenant_app(config_at(tmp_path))
    service, store = app.state.service, app.state.store
    try:
        assert service.kernel.memory is service.runtime.memory
        assert service.kernel.memory_worker is service.memory_worker
        sid = store.create_session()['id']
        source(service.kernel, scope='working', session_id=sid)
        for tool_name in ('search_knowledge', 'search_library'):
            async def run():
                budget = TurnMemoryBudget(1, 800)
                with RequestContext(session_id=sid):
                    records = await service.runtime._execute_tools_parallel([
                            {'id': 'engineering', 'name': 'tool_call', 'arguments': {
                                'name': tool_name, 'arguments': {'query': '工程检索'}}}], set(), 1, None, budget)
                return budget, records[0]
            budget, record = asyncio.run(run())
            assert budget.calls == 1 and budget.used_chars == 800
            assert not record['ok'] and record['budget_blocked']
            assert '未作为有效记忆注入' in record['content']
            with RequestContext(session_id=sid):
                good = asyncio.run(service.runtime.tools.execute(tool_name, {'query': '工程检索'}))
                assert good.ok and good.memory_dependencies
                assert service.runtime._memory_dependency_valid(good.memory_dependencies[0])
            assert not asyncio.run(service.runtime.tools.execute(tool_name,
                {'query': '工程检索', 'session_id': sid})).ok
    finally:
        store.close()


def test_api_and_legacy_source_creation_use_the_same_kernel(tmp_path):
    app = _create_tenant_app(config_at(tmp_path))
    try:
        client = TestClient(app)
        for prefix in ('/api/memory-kernel', '/api/library'):
            response = client.post(prefix + '/sources', json={'kind': 'note', 'title': '工程笔记', 'content': '工程检索笔记。'})
            assert response.status_code == 201
            sid = response.json()['id']
            assert client.get('/api/memory-kernel/sources/library/' + sid).status_code == 200
        result = client.post('/api/memory-kernel/search', json={'query': '工程检索', 'domains': ['library']})
        assert result.status_code == 200 and len(result.json()['evidence']) == 2
        assert client.post('/api/memory-kernel/search', json={'query': '工程检索', 'user_id': 'forged'}).status_code == 422
        assert client.get('/api/memory-kernel/sources/unknown/id').status_code == 422
        schema = _create_tenant_app(config_at(tmp_path / 'schema'), schema_only=True).openapi()
        assert '/api/memory-kernel/search' in schema['paths']
        assert not (tmp_path / 'schema' / 'test.db').exists()
    finally:
        app.state.store.close()


def test_gateway_isolates_source_search_read_and_session_context(tmp_path, monkeypatch):
    # Full authenticated gateway, two temporary tenants, no background network/model calls.
    from memoria.service import AgentService
    monkeypatch.setattr(AgentService, 'start_integrations', AsyncMock())
    app = create_app(config_at(tmp_path))
    with TestClient(app) as client:
        def login(name):
            body = {'username': name, 'password': 'Engineering-only-passphrase-2026'}
            assert client.post('/api/auth/register', json=body).status_code == 201
            result = client.post('/api/auth/login', json=body).json()
            return {'Authorization': 'Bearer ' + result['access_token']}
        a, b = login('kernel_a'), login('kernel_b')
        created = client.post('/api/memory-kernel/sources', headers=a,
            json={'kind': 'document', 'title': '工程来源', 'content': '工程检索账号隔离。'}).json()
        assert client.get('/api/memory-kernel/sources/library/' + created['id'], headers=b).status_code == 404
        assert client.post('/api/memory-kernel/search', headers=b,
            json={'query': '工程检索', 'domains': ['library']}).json()['evidence'] == []
        sid = client.post('/api/sessions', headers=a, json={'title': '工程会话'}).json()['id']
        assert client.post('/api/memory-kernel/search', headers=b,
            json={'query': '工程检索', 'session_id': sid}).status_code == 404
        client.cookies.clear()
        assert client.post('/api/memory-kernel/search', json={'query': '工程检索'}).status_code == 401
        assert client.get('/api/memory-kernel/sources/library/' + created['id']).status_code == 401
