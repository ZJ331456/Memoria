"""Temporary engineering inputs only; never benchmark or personal history labels."""
import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from memoria.memory.evidence_sufficiency import EvidenceSufficiency
from memoria.memory.answer_risk import AnswerRisk, query_risk
from memoria.memory.adaptive_fallback import AdaptiveFallback
from memoria.experiments import locomo_eval as bench
from memoria.experiments.history import HistoryWorkspace
from memoria.experiments.roles import load_roles
from memoria.config import Settings
from memoria.llm import ChatResult


def evidence():
    return [{'id':'source-a','text':'I adopted three dogs in 2020.','speaker':'Temporary A'},
            {'id':'source-b','text':'I live in Boston.','speaker':'Temporary B'}]


def sufficient():
    return {'state':'sufficient','requirements':[{'fact':'adoption','status':'supported',
        'sources':[{'id':'source-a','quote':'I adopted three dogs in 2020.'}]}],
        'missing_queries':[],'reason':'Explicit source.'}


def test_source_quotes_require_exact_membership_and_each_fact():
    decision = EvidenceSufficiency().validate(sufficient(), evidence())
    assert decision['quote_integrity_verified'] and not decision['semantic_sufficiency_verified']
    assert not decision['confidence_threshold_used']
    assert decision['verified_quotes'][0]['starts'] == [0]
    for change in ({'id':'foreign','quote':'I adopted three dogs in 2020.'},
                   {'id':'source-a','quote':'I adopted three cats in 2020.'}):
        candidate = sufficient()
        candidate['requirements'][0]['sources'] = [change]
        with pytest.raises(ValueError): EvidenceSufficiency().validate(candidate,evidence())
    candidate=sufficient(); candidate['requirements'][0]['sources']=[]
    with pytest.raises(ValueError): EvidenceSufficiency().validate(candidate,evidence())
    candidate=sufficient(); candidate['requirements'][0]['status']='missing'
    with pytest.raises(ValueError): EvidenceSufficiency().validate(candidate,evidence())


def test_conflict_needs_two_sources_and_no_cross_span_quotes():
    value={'state':'conflict','requirements':[{'fact':'location','status':'conflicted',
            'sources':[{'id':'source-b','quote':'I live in Boston.'}]}], 'missing_queries':[],'reason':'Conflict.'}
    with pytest.raises(ValueError): EvidenceSufficiency().validate(value,evidence())
    value['requirements'][0]['sources'].append({'id':'source-a','quote':'I adopted three dogs in 2020.'})
    assert EvidenceSufficiency().validate(value,evidence())['state']=='conflict'
    # Two approved spans must not be turned into one invented source sentence.
    item=[{'id':'source-a','text':'three dogs\nin 2020','source_texts':['three dogs','in 2020']}]
    candidate=sufficient();candidate['requirements'][0]['sources'][0]['quote']='dogs\nin 2020'
    with pytest.raises(ValueError): EvidenceSufficiency().validate(candidate,item)


@pytest.mark.parametrize('extra', [{'confidence':.99},{'answer':'GOLD_SENTINEL'},{'gold_evidence':['source-a']}])
def test_assessor_never_accepts_confidence_or_gold_fields(extra):
    with pytest.raises(ValueError): EvidenceSufficiency().validate({**sufficient(),**extra},evidence())


def support(basis='explicit'):
    return {'verdict':'supported','claims':[{'statement':'three dogs','relation':'supported','basis':basis,
        'sources':[{'id':'source-a','quote':'I adopted three dogs in 2020.'}], 'reason':'Direct.'}],
        'missing_queries':[],'reason':'Supported.'}


def test_answer_units_citations_and_inference_policy():
    answer={'answer':'three dogs','evidence_ids':['source-a']}
    assert AnswerRisk().validate(support(),'What did she adopt?',answer,evidence())['accepted']
    assert not AnswerRisk().validate(support('inference'),'What did she adopt?',answer,evidence())['accepted']
    assert AnswerRisk().validate(support('inference'),'What might she adopt?',answer,evidence())['accepted']
    for candidate in ({'answer':'three dogs and cats','evidence_ids':['source-a']},
                      {'answer':'three dogs','evidence_ids':['source-b']}):
        with pytest.raises(ValueError): AnswerRisk().validate(support(),'question',candidate,evidence())
    bad=support();bad['claims'][0]['statement']='other answer'
    with pytest.raises(ValueError): AnswerRisk().validate(bad,'question',answer,evidence())
    assert query_risk('When did she first visit?')['requires_timeline']
    assert query_risk('What disease was diagnosed?')['level']=='high'
    decision=AnswerRisk().validate(support('derived'),'What health issue was diagnosed?',answer,evidence())
    assert not decision['accepted'] and decision['rule_blockers']
    decision=AnswerRisk().validate(support(),'What did Temporary B adopt?',answer,evidence())
    assert not decision['accepted'] and 'requested_subject_not_grounded_in_quotes' in decision['rule_blockers']
    assert not AnswerRisk().validate(support(),'What was her first pet?',answer,evidence())['accepted']


def test_cumulative_budget_reserved_before_call_and_bounded_timeline():
    policy=AdaptiveFallback(max_calls=2,max_input_bytes=256)
    assert policy.reserve_call('gate',{'input_bytes':200,'fits':True})
    assert not policy.reserve_call('reader',{'input_bytes':60,'fits':True})
    assert policy.calls==1 and policy.input_bytes==200
    assert policy.reserve_call('reader',{'input_bytes':50,'fits':True})
    assert not policy.reserve_call('third',{'input_bytes':1,'fits':True})
    risk=query_risk('When did she first adopt dogs?')
    first=policy.plan('question',{'state':'missing_fact','missing_queries':['adoption date']},risk)
    assert first['route']=='related_timeline' and first['context_radius']==2
    assert policy.plan('question',{'missing_queries':[]},risk)['evidence_chars']==20000
    assert policy.plan('question',{},risk) is None
    assert not policy.public()['full_history_reader_allowed']


@pytest.mark.parametrize('kw',[{'evidence_budgets':(20000,12000)},{'max_calls':0},{'max_input_bytes':255},
                              {'evidence_budgets':(True,)},{'max_calls':True}])
def test_invalid_adaptive_limits(kw):
    with pytest.raises(ValueError): AdaptiveFallback(**kw)


def records():
    return [{'sample_id':f'conv-{i}','conversation':{'speaker_a':'Temporary A','speaker_b':'Temporary B',
        'session_1_date_time':'original date','session_1':[{'dia_id':'D1:1','speaker':'Temporary A',
        'text':'I adopted three dogs in 2020.'}]},
        'qa':[{'question':'What did Temporary A adopt?','category':c,'answer':'GOLD_SENTINEL',
               'evidence':['D1:1']} for c in (1,2,3,4)]} for i in range(10)]


@pytest.mark.parametrize('method',['targeted','adaptive_risk'])
def test_new_modes_freeze_no_gold_bound_calls_and_reject_foreign_sources(tmp_path,monkeypatch,method):
    raw=tmp_path/'source.json';raw.write_text(json.dumps(records()),encoding='utf-8')
    monkeypatch.setattr(bench,'SOURCE_SHA256',bench.digest_file(raw))
    from memoria.experiments import records as run_records
    monkeypatch.setattr(run_records,'code_identity',lambda root:{'commit':'engineering-fixture','branch':'test','dirty':False})
    class Retriever:
        document_encodes=0; query_encodes=0
        def __init__(self,*a,**kw): pass
        async def select(self,history,*a,**kw):
            self.query_encodes+=1
            return history,{'candidates':[]}
    monkeypatch.setattr(bench,'PrefixRetriever',Retriever)
    class Client:
        def __init__(self,settings,roles,recorder,timings):self.calls=[];self.recorder=recorder
        def public_roles(self):return {'reader':{'model':'test-only'},'judge':{'model':'test-only'}}
        async def call(self,role,messages,**kw):
            assert 'GOLD' not in json.dumps(messages)
            packet=json.loads(messages[1]['content']); source=packet['evidence'][0]
            call_id=len(self.calls)
            if messages[0]['content'].startswith('Assess'):
                output={'state':'sufficient','requirements':[{'fact':'adopted pets','status':'supported',
                    'sources':[{'id':source['id'],'quote':source['text']}]}],'missing_queries':[],'reason':'Explicit.'}
            elif 'proposed_answer' in packet:
                output={'verdict':'supported','claims':[{'statement':'three dogs','relation':'supported','basis':'explicit',
                    'sources':[{'id':source['id'],'quote':source['text']}],'reason':'Explicit.'}],
                    'missing_queries':[],'reason':'Supported.'}
            else:
                output={'status':'answer','answer':'three dogs','evidence_ids':[source['id']]}
                if method=='targeted' and call_id==0:
                    output={'status':'abstain','answer':'Not mentioned.','evidence_ids':[]}
            self.calls.append({'usage':{'total_tokens':10}})
            self.recorder.append('calls.jsonl',{'call':call_id,'role':role,'usage':{'total_tokens':10}})
            return ChatResult(content=json.dumps(output),usage={'total_tokens':10})
    settings=replace(Settings.load(Path(__file__).resolve().parents[1]/'config.example.toml'),root=tmp_path)
    workspace=HistoryWorkspace(tmp_path/'owner')
    result=asyncio.run(bench.execute(workspace,settings,raw,scope='development',fallback=method,
        allow_remote=True,client_factory=Client,embedding_factory=lambda cfg:None,roles=load_roles(None)))
    metrics=json.loads((result/'metrics.json').read_text())
    manifest=json.loads((result/'manifest.json').read_text())
    assert metrics['requested_cases']==8 and metrics['errors']==0
    assert manifest['protocol']['id'].startswith('locomo-sufficiency-risk-v1-'+method)
    assert metrics['reader_calls']==24 and metrics['automatic_memory_writes']==0
    rows=[json.loads(line) for line in (result/'cases.jsonl').read_text().splitlines()]
    assert all(r['adaptive_policy']['calls']<=6 and not r['adaptive_policy']['full_history_reader_allowed'] for r in rows)
    assert all(not r['semantic_entailment_verified'] for r in rows)
    assert all(r['adaptive_policy']['retrieval_rounds']==(1 if method=='targeted' else 0) for r in rows)
