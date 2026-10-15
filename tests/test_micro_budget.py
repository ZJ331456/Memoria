"""Budget-cap and gold-separation fixtures, never public benchmark claims."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from memoria.config import ModelConfig
from memoria.experiments.history import HistoryWorkspace,write_json
import memoria.experiments.micro_budget as micro
from memoria.experiments.locomo_eval import benchmark_cases
from memoria.experiments.locomo import sha


@pytest.fixture
def bundle(tmp_path,monkeypatch):
    folder=tmp_path/'frozen';folder.mkdir()
    cases=[{'id':'engineering-q'+str(i),'conversation_id':'engineering-conv','question':'Which coffee?'} for i in range(2)]
    history=[{'id':'engineering-conv/D1:1','dia_id':'D1:1','text':'Engineering coffee source.',
        'content':'Speaker: Engineering coffee source.','content_sha256':sha('Speaker: Engineering coffee source.'),
        'source_text_sha256':sha('Engineering coffee source.'),'speaker':'Speaker','session_date_raw':'unknown'}]
    (folder/'inputs.jsonl').write_text(''.join(json.dumps(c)+'\n' for c in cases))
    write_json(folder/'histories.json',{'engineering-conv':history})
    (folder/'gold.jsonl').write_text(''.join(json.dumps({'id':c['id'],'answer':'hidden gold','category':4})+'\n' for c in cases))
    monkeypatch.setattr(micro,'prepare',lambda *args:(folder,{'id':'engineering-protocol'},cases,{'engineering-conv':history}))
    settings=SimpleNamespace(root=Path(__file__).resolve().parents[1],main=ModelConfig('engineering-provider','placeholder','https://example.test/v1'),
        fast=ModelConfig('engineering-provider','placeholder','https://example.test/v1'))
    monkeypatch.setattr(micro,'account_settings',lambda s,*args:s)
    monkeypatch.setattr(micro,'answer_f1',lambda *args:0.5)
    class Budget:
        def __init__(self,settings):assert settings.reader_input_bytes==12000
        async def prepare(self,messages,**kwargs):
            assert kwargs['max_tokens']==512
            return SimpleNamespace(messages=messages,report={'input_bytes':len(json.dumps(messages).encode())})
    monkeypatch.setattr(micro,'ReaderBudget',Budget)
    return folder,HistoryWorkspace(tmp_path/'engineering-owner'),settings


def test_default_is_zero_call_multi_budget_same_pool(bundle):
    folder,w,settings=bundle
    def forbidden(*args):raise AssertionError('No client should be constructed')
    run=asyncio.run(micro.run_micro(w,settings,folder,client_factory=forbidden))
    metrics=json.loads((run/'metrics.json').read_text())
    assert metrics['actual_reader_calls']==0 and metrics['quality']=='unscored'
    events=[json.loads(s) for s in (run/'events.jsonl').read_text().splitlines()]
    assert len(events)==2
    for event in events:
        assert not event['gold_read'] and 'hidden gold' not in json.dumps(event)
        assert set(event['selections'])=={'1000','2000','3000'}
        for budget,groups in event['selections'].items():
            assert len(groups)==4 and all(g['cost']<=int(budget) for g in groups.values())


class Client:
    def __init__(self,settings,roles,recorder,timings):
        assert roles['reader'].max_retries==0 and roles['reader'].max_tokens==512
        self.calls=[]
    def public_roles(self):return {'reader':{'model':'engineering-provider'}}
    async def call(self,role,messages):
        assert 'hidden gold' not in json.dumps(messages)
        packet=json.loads(messages[1]['content']);eid=packet['selection']['evidence'][0]['id']
        self.calls.append({'usage':{'total_tokens':100}})
        return SimpleNamespace(content=json.dumps({'status':'answer','answer':'coffee','evidence_ids':[eid]}),tool_calls=[])


def test_reader_has_four_call_cap_matched_cases_budget_and_separate_gold(bundle):
    folder,w,settings=bundle
    run=asyncio.run(micro.run_micro(w,settings,folder,with_reader=True,allow_remote=True,client_factory=Client))
    metrics=json.loads((run/'metrics.json').read_text())
    assert metrics['actual_reader_calls']==4 and metrics['known_tokens_lower_bound']==400
    assert metrics['unknown_usage_calls']==0 and metrics['production_writes']==0
    assert all(m['requested']==2 and m['mean_token_f1']==0.5 for m in metrics['methods'].values())
    cases=[json.loads(s) for s in (run/'cases.jsonl').read_text().splitlines()]
    assert len(cases)==4
    assert len({r['candidate_pool_sha256'] for r in cases})==1


def test_remote_permission_guard_prevents_calls(bundle):
    folder,w,settings=bundle
    with pytest.raises(ValueError,match='allow-remote'):
        asyncio.run(micro.run_micro(w,settings,folder,with_reader=True,client_factory=Client))


def test_failures_and_unknown_usage_keep_four_row_denominator(bundle):
    folder,w,settings=bundle
    class Broken(Client):
        async def call(self,*args):
            self.calls.append({'usage':None});raise ValueError('injected failure')
    run=asyncio.run(micro.run_micro(w,settings,folder,with_reader=True,allow_remote=True,client_factory=Broken))
    metrics=json.loads((run/'metrics.json').read_text())
    assert metrics['actual_reader_calls']==4 and metrics['errors']==4
    assert metrics['unknown_usage_calls']==4 and metrics['known_tokens_lower_bound'] is None
    assert all(m['requested']==2 and m['errors']==2 and m['mean_token_f1']==0 for m in metrics['methods'].values())


def test_more_than_two_frozen_questions_rejected_before_call(bundle):
    folder,w,settings=bundle
    with (folder/'inputs.jsonl').open('a') as f:f.write(json.dumps({'id':'extra'})+'\n')
    with pytest.raises(ValueError,match='exactly two'):
        asyncio.run(micro.run_micro(w,settings,folder,with_reader=True,allow_remote=True,client_factory=Client))


def test_micro_selection_uses_first_two_source_order_questions_not_answers_or_category(monkeypatch):
    import memoria.experiments.locomo_eval as module
    history=[{'dia_id':'D1:1','id':'conv-1/D1:1'}]
    monkeypatch.setattr(module,'smoke_cases',lambda records:([{'id':'conv-1-q0','conversation_id':'conv-1','history':history}],[]))
    records=[{'sample_id':'conv-1','qa':[{'question':'Q'+str(i),'category':5 if i==0 else 4,'answer':'hidden','evidence':['D1:1']} for i in range(5)]}]
    cases,histories,gold=benchmark_cases(records,'micro')
    assert [c['id'] for c in cases]==['conv-1-q0','conv-1-q1']
    assert all(set(c)=={'id','conversation_id','question'} for c in cases)
    assert len(gold)==2 and gold[0]['category']==5


def test_transport_failure_is_not_semantic_refusal_and_never_serializes_provider_body():
    import httpx
    from memoria.llm.types import ProviderError
    from memoria.observability.memory_trace import execution_failure
    try:
        try:raise httpx.ConnectError('private transport detail')
        except httpx.ConnectError as cause:raise ProviderError('private provider response') from cause
    except ProviderError as exc:
        detail=execution_failure(exc,'call')
    assert detail['failure_stage']=='reader_transport_failure'
    assert 'private' not in json.dumps(detail)
    assert execution_failure(ValueError('bad JSON'),'parse')['failure_stage']=='reader_contract_failure'


def test_failed_reader_does_not_count_as_a_reasonable_abstention():
    from memoria.observability.memory_trace import attribute
    item={'source_ids':['engineering-source']}
    packet={'candidates':[item],'selection':{'payload':{'evidence':[item]}},'state':None}
    assert attribute(packet,required_source_ids=['engineering-source'],reader={'status':'error'})['stage']=='reader_execution_failure'
