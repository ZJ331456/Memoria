"""Isolated engineering histories; no production writes or research quality labels."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from memoria.claims import ClaimMemory
from memoria.experiments.history import HistoryWorkspace
from memoria.experiments.production_replay import capture_production, replay_file
from memoria.memory import MemoryEngine
from memoria.memory.access import MemoryAccessOrchestrator, QueryRequest
from memoria.observability.memory_trace import attribute, replay, watermark
from memoria.store import Store


@pytest.fixture
def core(tmp_path):
    store=Store(tmp_path/'trace.db','json')
    claims=ClaimMemory(store,'engineering-owner')
    sid=store.create_session('Engineering lifecycle')['id']
    yield store,claims,sid
    store.close()


def packet(core):
    core[0].add_message(core[2],'user','Engineering cedar source with complementary observations.')
    return asyncio.run(MemoryAccessOrchestrator(core[1],MemoryEngine(core[0])).access(QueryRequest(query='cedar')))


def test_lifecycle_watermarks_are_transactional_and_reopen_does_not_invent_history(core,tmp_path):
    before=watermark(core[0])
    with pytest.raises(RuntimeError):
        with core[0]._memory_transaction():
            core[0].add_message(core[2],'user','Engineering rollback')
            raise RuntimeError('rollback')
    assert watermark(core[0])==before
    msg=core[0].add_message(core[2],'user','Engineering source')
    job=core[0].enqueue_memory_job(msg['id'],msg['content'],'Engineering assistant')
    after=watermark(core[0])
    assert after['epochs']['source']>before['epochs']['source']
    assert after['epochs']['extraction']>before['epochs']['extraction'] and after['jobs']=={'pending':1}
    other=Store(tmp_path/'trace.db','json')
    try:
        ClaimMemory(other,'engineering-owner')
        assert watermark(other)==after
    finally:other.close()
    assert job['id'] in [r[0] for r in core[0].db.execute("SELECT entity_id FROM memory_trace_events WHERE stage='extraction'")]
    assert 'Engineering source' not in json.dumps([dict(r) for r in core[0].db.execute('SELECT * FROM memory_trace_events')])


def test_frozen_pool_reproduces_original_selection_and_budget_sweep_without_models(core):
    value=packet(core)
    result=replay(value,'engineering-owner',budgets=[1000,3000,6000])
    assert result['model_calls']==0 and result['quality']=='unscored'
    assert result['selections']['6000']['coverage']==value['selection']
    for limit, variants in result['selections'].items():
        assert set(variants)=={'topk','coverage','mmr','evidence_coverage'}
        assert all(v['cost']<=int(limit) for v in variants.values())
    core[0].undo_memory_sources(value['candidates'][0]['source_ids'])
    assert replay(value,'engineering-owner')  # Offline historical capture, never reused as live evidence.


@pytest.mark.parametrize('field',['candidates','requirements','selection','state','production_trace'])
def test_mutated_capture_rejected(core,field):
    value=packet(core)
    if field=='state':value[field]={'changed':True}
    elif field=='candidates':value[field][0]['data']['text']='modified'
    else:value[field]['tampered']=True
    with pytest.raises(ValueError,match='fingerprint'):replay(value,'engineering-owner')


def test_cross_owner_and_untrusted_token_replay_rejected(core):
    value=packet(core)
    with pytest.raises(ValueError,match='another account'):replay(value,'other')
    from memoria.observability.memory_trace import fingerprint
    trace=value['production_trace'];trace['request']['budget_unit']='reader_tokens'
    trace['request_sha256']=fingerprint(trace['request'])
    trace['trace_sha256']=fingerprint({k:v for k,v in trace.items() if k!='trace_sha256'})
    with pytest.raises(ValueError,match='tokenizer'):replay(value,'engineering-owner')


def test_failure_attribution_does_not_guess_gold_or_change_pool(core):
    value=packet(core); frozen=copy.deepcopy(value)
    ids=value['candidates'][0]['source_ids']
    assert attribute(value)['stage']=='unlabelled'
    assert attribute(value,required_source_ids=ids,source_inventory=[])['stage']=='source_not_written_or_unavailable'
    assert attribute(value,required_source_ids=['unseen'])['stage']=='not_retrieved'
    value['selection']['payload']['evidence']=[]
    assert attribute(value,required_source_ids=ids)['stage']=='candidate_not_presented'
    value=frozen
    assert attribute(value,required_source_ids=ids)['stage']=='presented_reader_not_run'
    assert attribute(value,required_source_ids=ids,reader={'status':'answer'},answer_correct=False)['stage']=='reader_answer_or_support_failure'
    assert attribute(value,required_source_ids=ids,reader={'status':'answer'})['stage']=='answered_unscored'
    assert attribute(value,required_source_ids=ids)['semantic_sufficiency'] is None


def test_snapshot_cli_uses_immutable_input_private_owner_and_no_live_writes(tmp_path):
    workspace=HistoryWorkspace(tmp_path/'engineering-owner')
    store=Store(workspace.path('memory','memoria.db'),'json')
    ClaimMemory(store,workspace.owner)
    sid=store.create_session('Engineering snapshot')['id']
    store.add_message(sid,'user','Engineering cedar private source.')
    before=watermark(store)
    settings=SimpleNamespace(root=Path(__file__).resolve().parents[1])
    try:
        run=asyncio.run(capture_production(workspace,settings,{'query':'cedar'}))
        assert watermark(store)==before
        capture=run/'production-capture.json'
        result=json.loads(capture.read_text(encoding='utf-8'))
        assert result['candidates'] and result['production_trace']['owner_user_id']==workspace.owner
        assert (run/'account-snapshot.db').is_file()
        rerun=replay_file(workspace,settings,capture,budgets=[1000,2000])
        assert json.loads((rerun/'metrics.json').read_text())['model_calls']==0
        other=HistoryWorkspace(tmp_path/'other-owner')
        with pytest.raises(ValueError,match="this account"):replay_file(other,settings,capture)
        with pytest.raises(ValueError,match='no LLM'):asyncio.run(capture_production(workspace,settings,{'query':'cedar','use_query_model':True}))
    finally:store.close()


def test_access_marks_writes_during_async_retrieval_without_claiming_snapshot_consistency(core):
    store,claims,sid=core
    store.add_message(sid,'user','Engineering cedar before retrieval.')
    class RacingEngine:
        async def retrieve(self,*args,**kwargs):
            store.add_message(sid,'user','Engineering source arriving during await.')
            return []
    value=asyncio.run(MemoryAccessOrchestrator(claims,RacingEngine()).access(QueryRequest(query='cedar')))
    assert 'source' in value['production_trace']['changed_stages']
    assert value['production_trace']['before']['event_seq']<value['production_trace']['after']['event_seq']
    assert replay(value,'engineering-owner')['quality']=='unscored'
