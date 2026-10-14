"""Temporary engineering inputs, not personal data or research quality scores."""
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from memoria.api import create_app
from memoria.api.research_runs import research_runs_router
from fastapi import FastAPI
from memoria.claims import ClaimDecision, ClaimDraft, ClaimError, ClaimMemory, ExtractionRequest, sha
from memoria.llm import ChatResult
from memoria.memory.access import MemoryAccessOrchestrator, QueryRequest
from memoria.memory.claim_analysis import AnalysisRequest, analyze_claim
from memoria.memory.engine import MemoryEngine
from memoria.memory.evidence_selector import select_evidence
from memoria.memory.temporal_query import parse_query
from memoria.store import Store


@pytest.fixture
def state(tmp_path):
    store = Store(tmp_path / 'temporary.db', vector_backend='json')
    claims = ClaimMemory(store, 'temporary-owner')
    sid = store.create_session('Engineering only')['id']
    yield store, claims, sid
    store.close()


def proposal(state, value='中文', **kwargs):
    store, claims, sid = state
    text = '工程原话：' + value
    msg = store.add_message(sid, 'user', text)
    e = dict(message_id=msg['id'], start=0, end=len(text), quote=text, content_sha256=sha(text))
    return claims.create(ClaimDraft(predicate='response_language', value=value, evidence=[e], **kwargs))


def approve(state, c):
    return state[1].approve(c['id'], ClaimDecision(expected_revision=c['revision'], reason='工程明确批准'))


@pytest.mark.parametrize('text,precision,start,end', [
    ('我去年住在哪里', 'year', '2025-01-01', '2026-01-01'),
    ('我上个月住在哪里', 'month', '2025-12-01', '2026-01-01'),
    ('2024年2月我住哪里', 'month', '2024-02-01', '2024-03-01'),
    ('我2024年2月29日住哪里', 'day', '2024-02-29', '2024-03-01'),
    ('我前天住哪里', 'day', '2025-12-31', '2026-01-01'),
])
def test_calendar_is_range_not_fake_instant(text, precision, start, end):
    parsed = parse_query(text, datetime(2026, 1, 2, tzinfo=timezone.utc))
    assert parsed['precision'] == precision
    assert parsed['range_start'].startswith(start) and parsed['range_end'].startswith(end)
    assert not parsed['requires_clarification']


@pytest.mark.parametrize('text', ['我2025年2月30日住哪里', '我去年和今年住哪里', '我上周住哪里', '我朋友住哪里',
                                 '我2026/05/01住哪里', '我三个月前住哪里', '我明天住哪里', '我2025年初住哪里', '我什么时候喜欢中文', '我好友住哪里', '我1800年住哪里'])
def test_ambiguous_or_invalid_query_clarifies(text):
    assert parse_query(text)['requires_clarification']


def test_fixed_pool_selection_budget_and_atomic_constraints():
    candidates = [dict(id='raw', kind='source', score=3, covers=[], source_ids=['x'], data='a'),
                  dict(id='same-source', kind='source', score=2, covers=[], source_ids=['x'], data='b'),
                  dict(id='state', kind='claim', score=1, covers=['diet'], source_ids=['y'], data=[{'negative': True, 'scope': {'activity': 'interview'}}])]
    top = select_evidence(candidates, ['diet'], 1000, 'topk', 1)
    coverage = select_evidence(candidates, ['diet'], 1000, 'coverage', 1)
    assert top['candidate_ids'] == coverage['candidate_ids']
    assert coverage['payload']['evidence'][0]['id'] == 'state' and coverage['uncovered'] == []
    limited = select_evidence(candidates, ['diet'], 256)
    assert limited['cost_chars'] <= 256
    assert len([c for c in select_evidence(candidates, [], 2000)['payload']['evidence'] if c['kind']=='source']) == 1


def test_claim_query_views_and_conditions_do_not_fall_through(state):
    store, claims, _ = state
    approve(state, proposal(state))
    orchestrator = MemoryAccessOrchestrator(claims, MemoryEngine(store))
    for expected in ('retrieve', 'view'):
        response = asyncio.run(orchestrator.access(QueryRequest(query='我用什么语言回答')))
        assert response['path'] == expected
        assert response['selection']['covered'] == ['slot:response_language']
    approve(state, proposal(state, '英文', scope={'activity':'interview'}))
    response = asyncio.run(orchestrator.access(QueryRequest(query='我用什么语言回答')))
    assert response['path'] == 'clarify' and not response['selection']['payload']['evidence']
    specific = asyncio.run(orchestrator.access(QueryRequest(query='面试模拟用什么语言回答')))
    assert specific['selection']['payload']['evidence'][0]['data'][0]['value'] == '英文'
    conflict = asyncio.run(orchestrator.access(QueryRequest(query='面试模拟用什么语言回答', context={'activity':'vacation'}, fact_as_of='2026-01-01T00:00:00Z')))
    assert conflict['path']=='clarify' and conflict['parsed']['requires_clarification']


def test_explicit_source_lane_does_not_secretly_include_claim_lookup(state):
    approve(state, proposal(state))
    response = asyncio.run(MemoryAccessOrchestrator(state[1], MemoryEngine(state[0])).access(QueryRequest(query='我的回答语言', mode='dense')))
    assert response['state'] is None and not response['candidates'] and response['path']=='clarify'
    response = asyncio.run(MemoryAccessOrchestrator(state[1], MemoryEngine(state[0])).access(QueryRequest(query='去年我的回答语言', mode='dense')))
    assert response['path']=='clarify' and 'calendar_range_not_supported' in response['reason']


def test_raw_source_never_fills_claim_state_and_deleted_source_rejected(state):
    store, claims, sid = state
    msg = store.add_message(sid, 'user', '工程原話：回答语言是中文')
    store.add_memory('回答语言是中文', 'preference', source_ref=msg['id'])
    orchestrator = MemoryAccessOrchestrator(claims, MemoryEngine(store))
    result = asyncio.run(orchestrator.access(QueryRequest(query='回答语言是中文')))
    assert result['path']=='clarify' and result['candidates'][0]['kind']=='source'
    assert not result['candidates'][0]['data']['approved_personal_state']
    store.delete_session(sid)
    result = asyncio.run(orchestrator.access(QueryRequest(query='回答语言是中文')))
    assert result['candidates']==[] and result['path']=='clarify'
    assert asyncio.run(MemoryEngine(store).retrieve('回答语言是中文'))==[]  # Filter before ranking/Top-K.


def test_recorded_query_never_semantically_looks_into_future(state):
    store, claims, _ = state
    class Forbidden:
        async def retrieve(self, *args, **kwargs):
            raise AssertionError('no semantic knowledge time dimension')
    result = asyncio.run(MemoryAccessOrchestrator(claims, Forbidden()).access(QueryRequest(
        query='我用什么语言回答', recorded_as_of='2020-01-01T00:00:00Z')))
    assert result['path']=='clarify' and result['candidates']==[]


@pytest.mark.parametrize('query', ['我朋友上周住哪里', '我朋友去年和今年住哪里', '我朋友2025年2月30日住哪里'])
def test_resolving_identity_or_time_does_not_silently_resolve_other_ambiguity(state, query):
    orchestrator = MemoryAccessOrchestrator(state[1], MemoryEngine(state[0]))
    identity_only = asyncio.run(orchestrator.access(QueryRequest(query=query, subject_id='entity:friend')))
    assert identity_only['parsed']['requires_clarification'] and not identity_only['candidates']
    time_only = asyncio.run(orchestrator.access(QueryRequest(query=query, fact_as_of='2026-01-01T00:00:00Z')))
    assert time_only['parsed']['requires_clarification'] and time_only['parsed']['subject_requires_clarification']


def test_timeline_keeps_unknown_time_and_pending_label(state):
    c = proposal(state)
    result = asyncio.run(MemoryAccessOrchestrator(state[1], MemoryEngine(state[0])).access(QueryRequest(query='我去年回答语言是什么')))
    assert result['path']=='history' and result['timeline_count']==1
    item = result['candidates'][0]['data']
    assert item['status']=='pending' and item['fact_start'] is None and not item['historical_state_verified']


def test_historical_timeline_hides_future_retirement_and_revisions(state):
    c = approve(state, proposal(state))
    cutoff = c['recorded_at']
    state[1].decide(c['id'], ClaimDecision(expected_revision=2, reason='工程撤回'), 'retract')
    req = QueryRequest(query='2026年我用什么语言回答', recorded_as_of=cutoff)
    parsed = parse_query(req.query)
    items = MemoryAccessOrchestrator(state[1], None).timeline(req, parsed)
    assert [i['revision'] for i in items]==[1,2] and items[-1]['retired_at'] is None


def test_supplement_filters_public_source_and_revalidates_after_await(state):
    store, claims, sid = state
    msg = store.add_message(sid, 'user', '工程来源 徒步')
    item = store.add_memory('工程来源 徒步', source_ref=msg['id'])
    with store.db:
        store.db.execute('INSERT INTO dataset_imports VALUES(?,?,?,?,?)', ('public', 'sha', '{}',
            json.dumps({'source_mapping':[{'message_ids':[msg['id']]}]}), '2026-01-01'))
    result = asyncio.run(MemoryAccessOrchestrator(claims, MemoryEngine(store)).access(QueryRequest(query='徒步')))
    assert not result['candidates'] and result['rejected'][0]['reason']=='source_unavailable_revoked_or_public'
    class Changed:
        async def retrieve(self, *args, **kwargs):
            candidate = {**item, 'retrieval': {'score':1}}
            with store.db:
                store.db.execute("UPDATE memories SET content='changed' WHERE id=?", (item['id'],))
            return [candidate]
    result = asyncio.run(MemoryAccessOrchestrator(claims, Changed()).access(QueryRequest(query='徒步')))
    assert not result['candidates'] and result['rejected'][0]['reason']=='memory_changed_during_retrieval'


def test_review_aid_is_advisory_and_rechecks_source_after_model(state):
    c = proposal(state)
    async def supported(*args, **kwargs):
        return ChatResult(content='{"assessment":"Supported"}', raw_message={})
    llm = SimpleNamespace(chat=supported)
    result = asyncio.run(analyze_claim(state[1], llm, c['id'], AnalysisRequest(expected_revision=1, use_model=True)))
    assert result['assessment']=='Supported' and not result['persisted'] and not result['semantic_support_verified']
    assert state[1].get(c['id'])['status']=='pending'
    async def race(*args, **kwargs):
        state[0].delete_session(state[2])
        return await supported()
    with pytest.raises(ClaimError, match='作废'):
        asyncio.run(analyze_claim(state[1], SimpleNamespace(chat=race), c['id'], AnalysisRequest(expected_revision=1, use_model=True)))


def test_identical_scope_suggestion_requires_explicit_reinforcement(state):
    approved = approve(state, proposal(state))
    duplicate = proposal(state)
    advice = asyncio.run(analyze_claim(state[1], None, duplicate['id'], AnalysisRequest(expected_revision=1)))
    assert advice['update']['action']=='reinforce' and advice['update']['targets'][0]['id']==approved['id']
    assert state[1].get(duplicate['id'])['status']=='pending'


def test_condition_extraction_is_opt_in_source_bound_pending_only(state):
    store, claims, sid = state
    text = '面试模拟时用英文回答'
    msg = store.add_message(sid, 'user', text)
    async def extracted(*args, **kwargs):
        return ChatResult(content='{"claims":[{"predicate":"response_language","value":"英文","evidence_quote":"'+text+'","scope":{"activity":"interview"},"condition_quote":"'+text+'"}]}', raw_message={})
    llm = SimpleNamespace(chat=extracted)
    with pytest.raises(ClaimError):
        asyncio.run(claims.extract(llm, ExtractionRequest(message_id=msg['id'])))
    assert claims.list()==[]
    result = asyncio.run(claims.extract(llm, ExtractionRequest(message_id=msg['id'], infer_conditions=True)))
    assert result['candidates'][0]['scope']=={'activity':'interview'} and result['automatic_approvals']==0
    async def dropped_condition(*args, **kwargs):
        return ChatResult(content=json.dumps({'claims':[{'predicate':'response_language','value':'英文','evidence_quote':text}]}), raw_message={})
    inferred = asyncio.run(claims.extract(SimpleNamespace(chat=dropped_condition), ExtractionRequest(message_id=msg['id'], infer_conditions=True, scope={'travel':True})))
    assert inferred['candidates'][0]['scope']=={'travel':True,'activity':'interview'}
    assert len(claims.list())==2  # Source rules recover the condition even if the model omitted it.
    assert all(c['status']=='pending' for c in claims.list())


def test_separate_lane_modes_lexical_never_call_encoder_and_dense_does_not_fake_fallback(state):
    store, _, _ = state
    store.add_memory('工程检索 中文回答')
    class Forbidden:
        enabled = True
        namespace = 'test'
        async def embed(self, *args):
            raise AssertionError('lexical must not encode')
    engine = MemoryEngine(store, Forbidden())
    for mode in ('keyword','bm25'):
        diag = {}
        result = asyncio.run(engine.retrieve('中文回答', mode=mode, diagnostics=diag, expand_graph=False))
        assert result and not diag['dense_available'] and diag['mode']==mode
    diag = {}
    assert asyncio.run(MemoryEngine(store).retrieve('中文回答', mode='dense', diagnostics=diag))==[]
    assert not diag['dense_available'] and not diag['fallback']


def test_authenticated_query_analysis_source_ownership_and_public_shell(tmp_path):
    config = tmp_path/'config.toml'
    config.write_text(f'[storage]\ndatabase="{(tmp_path/"legacy.db").as_posix()}"\n[agent.mcp]\nenabled=false\n[memory.markdown]\nenabled=false\n')
    app = create_app(config)
    with TestClient(app) as client:
        assert client.post('/api/memory-query', json={'query':'工程查询'}).status_code==401
        for username in ('alice','bob'):
            credentials = {'username':username,'password':'Temporary-engineering-password'}
            assert client.post('/api/auth/register', json=credentials).status_code==201
            response = client.post('/api/auth/login', json=credentials).json()
            if username=='alice':
                uid, token = response['user']['id'], response['access_token']
        alice = {'Authorization':'Bearer '+token}
        sid = client.post('/api/sessions', json={'title':'Engineering'}, headers=alice).json()['id']
        tenant = app.state.tenants.apps[uid]
        msg = tenant.state.store.add_message(sid, 'user', '工程中文')
        source = client.get('/api/claim-source/'+msg['id'], headers=alice).json()
        assert source['personal_claim_eligible'] and source['evidence']['content_sha256']==sha(msg['content'])
        assert client.get('/api/claim-source/'+msg['id']).status_code==404  # Bob cookie
        c = client.post('/api/claims', json={'predicate':'response_language','value':'中文','evidence':[source['evidence']]}, headers=alice).json()
        assert client.post('/api/claims/'+c['id']+'/analyze', json={'expected_revision':1}).status_code==404
        assert client.post('/api/memory-query', json={'query':'我的回答语言'}, headers=alice).json()['path']=='clarify'
        assert client.post('/api/memory-query', json={'query':'工程查询','user_id':uid}, headers=alice).status_code==422
        paths = client.get('/openapi.json').json()['paths']
        assert paths['/api/memory-query']['post']['security']==[{'UserSession':[]}]
        assert '/api/claims/{claim_id}/analyze' in paths
        shell = client.get('/studio/')
        assert shell.status_code in (200,503) and shell.headers['cache-control']=='no-store'
        same_origin = client.post('/api/memory-query', json={'query':'工程查询'}, headers={**alice,'Origin':'http://testserver'})
        assert same_origin.status_code==200
        assert client.post('/api/memory-query', json={'query':'工程查询'}, headers={**alice,'Origin':'https://external.invalid'}).status_code==403
    assert not (tmp_path/'legacy.db').exists()


def test_readonly_research_records_restrict_owner_paths_hashes_and_private_fields(tmp_path):
    root = tmp_path/'experiments'
    run_id = '20261012T010203Z-abcdef12'
    path = root/run_id
    path.mkdir(parents=True)
    raw = json.dumps({'requested_cases':2,'errors':1,'quality':{'human_correct':False},'prompt':'PRIVATE ORIGINAL','api_key':'SECRET'}).encode()
    (path/'metrics.json').write_bytes(raw)
    import hashlib
    manifest = {'run_id':run_id,'protocol':{'owner_user_id':'owner','baseline':'B0_recent','private_field':'SECRET'},
                'models':{'reader':{'model':'engineering-model','api_key':'SECRET','endpoint':'PRIVATE'}},
                'artifacts':{'metrics.json':{'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}}}
    (path/'manifest.json').write_text(json.dumps(manifest))
    app = FastAPI(); app.include_router(research_runs_router(root,'owner'))
    with TestClient(app) as client:
        index = client.get('/api/research-runs').json()
        assert len(index['runs'])==1 and 'SECRET' not in json.dumps(index) and 'PRIVATE' not in json.dumps(index)
        response = client.get('/api/research-runs/'+run_id)
        assert response.status_code==200 and response.json()['artifact_verified']
        assert response.json()['metrics']['quality']['human_correct'] is None
        assert 'SECRET' not in response.text and 'PRIVATE' not in response.text
        assert client.post('/api/research-runs/'+run_id).status_code==405
        (path/'metrics.json').write_text('{}')
        assert client.get('/api/research-runs/'+run_id).status_code==404
        manifest['protocol']['owner_user_id']='other-owner'
        (path/'manifest.json').write_text(json.dumps(manifest))
        assert client.get('/api/research-runs').json()['unavailable']==1
        assert client.get('/api/research-runs/'+run_id).status_code==404
        assert client.get('/api/research-runs/not-a-run').status_code==404
