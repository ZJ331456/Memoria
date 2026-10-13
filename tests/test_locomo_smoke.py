"""Temporary engineering inputs only; never imported or reported as benchmark quality."""
import asyncio
import hashlib
import json

import pytest

from memoria.config import Settings
from memoria.experiments import locomo
from memoria.experiments.history import HistoryWorkspace
from memoria.experiments.roles import load_roles
from memoria.llm import ChatResult


def fixture_records():
    return [{'sample_id':f'conv-{i}', 'conversation':{
        'speaker_a':'Temporary A', 'speaker_b':'Temporary B', 'session_1_date_time':'original date text',
        'session_1':[{'dia_id':'D1:1','speaker':'Temporary A','text':'Original temporary engineering source.'}]},
        'qa':[{'category':c, 'question':'Temporary engineering query?', 'answer':'GOLD_SENTINEL',
               'evidence':['D1:1']} for c in (1,2,3,4)]} for i in range(10)]


@pytest.fixture
def source(tmp_path, monkeypatch):
    path=tmp_path/'locomo10.json'; path.write_text(json.dumps(fixture_records()),encoding='utf-8')
    monkeypatch.setattr(locomo,'SOURCE_SHA256',hashlib.sha256(path.read_bytes()).hexdigest())
    return path


def test_original_speaker_date_and_text_preserved_without_gold():
    inputs,gold=locomo.smoke_cases(fixture_records())
    assert len(inputs)==len(gold)==10
    assert [g['category'] for g in gold]==[1,2,3,4,1,2,3,4,1,2]
    assert 'GOLD_SENTINEL' not in json.dumps(inputs)
    assert all('category' not in case and 'evidence_ids' not in case for case in inputs)
    item=inputs[0]['history'][0]
    assert item['session_date_raw']=='original date text'
    assert item['text']=='Original temporary engineering source.'
    assert item['content']=='Temporary A: Original temporary engineering source.'


def test_sampling_does_not_change_with_answers_or_evidence():
    original=fixture_records(); inputs,_=locomo.smoke_cases(original)
    for record in original:
        for q in record['qa']:q['answer']='different label';q['evidence']=['missing label']
    changed,gold=locomo.smoke_cases(original)
    assert inputs==changed and all(g['unresolved_evidence_ids'] for g in gold)


def test_bundle_is_immutable_and_source_sha_is_checked(tmp_path,source):
    workspace=HistoryWorkspace(tmp_path/'temporary-owner')
    bundle,protocol,_=locomo.prepare_smoke(workspace,source)
    assert not (workspace.directory/'memory').exists()
    assert locomo.prepare_smoke(workspace,source)[1]==protocol
    (bundle/'inputs.jsonl').write_text('changed',encoding='utf-8')
    with pytest.raises(ValueError,match='changed'):locomo.prepare_smoke(workspace,source)
    source.write_text('changed',encoding='utf-8')
    with pytest.raises(ValueError,match='SHA'):locomo.prepare_smoke(workspace,source)


@pytest.mark.parametrize('change',['foreign','duplicate','missing','duplicate_key','tool'])
def test_citation_and_schema_failures_are_rejected(change):
    value={'status':'answer','answer':'Temporary answer','evidence_ids':['conv-0/D1:1']}
    if change=='foreign':value['evidence_ids']=['other-conversation/D1:1']
    if change=='duplicate':value['evidence_ids']*=2
    if change=='missing':value['evidence_ids']=[]
    result=ChatResult(content=json.dumps(value))
    if change=='duplicate_key':result.content=result.content[:-1]+',"answer":"changed"}'
    if change=='tool':result.tool_calls=[{'name':'write','arguments':{}}]
    with pytest.raises(ValueError):locomo.parse_answer(result,['conv-0/D1:1'])


def test_failed_cases_and_unresolved_gold_remain_in_denominator():
    inputs,gold=locomo.smoke_cases(fixture_records())
    rows=[{'id':i['id'],'status':'error'} for i in inputs]
    rows[0].update(status='success',answer='GOLD_SENTINEL',presented_ids=list(gold[0]['evidence_ids']),evidence_ids=list(gold[0]['evidence_ids']))
    gold[0]['evidence_ids'].append('conv-0/missing');gold[0]['unresolved_evidence_ids']=['conv-0/missing']
    score=locomo.score_smoke(rows,gold)
    assert score['execution_statuses']=={'success':1,'error':9}
    assert score['details'][0]['presented_evidence_recall']==.5
    assert score['unresolved_gold_evidence_count']==1 and score['official_score'] is None
    assert score['diagnostic_unstemmed_token_f1_all_cases']==.1


def test_execute_uses_only_input_sources_and_exactly_ten_reader_calls(tmp_path,source):
    path=tmp_path/'config.toml';path.write_text('[storage]\ndatabase="temporary.db"\n[llm.main]\nmodel="fixture"\napi_key="x"\nbase_url="http://example.test/v1"\n')
    class Reader:
        def __init__(self,settings,roles,recorder,timings):self.calls=[];self.timings=timings
        def public_roles(self):return {'reader':{'model':'engineering-only'}}
        async def call(self,role,messages,stream=False):
            assert 'GOLD_SENTINEL' not in json.dumps(messages)
            packet=json.loads(messages[-1]['content'])
            assert set(packet)=={'question','evidence','allowed_evidence_ids'}
            self.calls.append({'usage':None})
            ids=packet['allowed_evidence_ids']
            return ChatResult(content=json.dumps({'status':'answer','answer':'Engineering only','evidence_ids':ids[:1]}))
    workspace=HistoryWorkspace(tmp_path/'temporary-owner')
    roles=load_roles(None)
    from dataclasses import replace
    roles={k:replace(v,thinking='provider_default_unknown') for k,v in roles.items()}
    result=asyncio.run(locomo.execute_smoke(workspace,Settings.load(path),source,roles=roles,allow_remote=True,
                                           baseline='B0_recent',client_factory=Reader))
    metrics=json.loads((result/'metrics.json').read_text(encoding='utf-8'))
    assert metrics['reader_calls']==10 and metrics['execution_statuses']=={'success':10}
    assert metrics['automatic_memory_writes']==0 and not (workspace.directory/'memory').exists()
    assert not metrics['usage_known_calls']
