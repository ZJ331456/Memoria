"""Synthetic acceptance infrastructure tests; substitutes here are not paid model runs."""
import asyncio
import copy
import json
from pathlib import Path

import pytest

from memoria.experiments import simulation, records
from memoria.experiments.production_replay import execute_closed_loop
from tests.test_closed_loop import environment, Model, document


def synthetic_document():
    payload=document();payload['provenance']='[SYNTHETIC] Engineering-only temporary input'
    payload['subject_key']='synthetic:engineering'
    for event in payload['events']:
        if event['kind']=='message':event['actor']='synthetic:engineering'
    return payload


def test_all_fixed_synthetic_cases_validate_and_have_separate_expectations():
    root=Path(__file__).resolve().parents[1]
    suite=simulation.load_suite(root)
    expected=json.loads((root/'simulation/expectations.json').read_text(encoding='utf-8'))
    assert len(suite['cases'])>=27
    assert {c['id'] for c in suite['cases']}==set(expected['cases'])
    for case in suite['cases']:
        assert {e['id'] for e in case['input']['events'] if e['kind']=='question'}==set(expected['cases'][case['id']])
        assert 'gold' not in case['input'] and 'expectations' not in case['input']


def test_synthetic_runner_refuses_unlabelled_input_or_output_escape(environment):
    workspace,settings,tmp=environment
    settings.root=tmp
    path=tmp/'input.json';path.write_text(json.dumps(document()),encoding='utf-8')
    with pytest.raises(ValueError,match='explicit provenance'):
        asyncio.run(execute_closed_loop(workspace,settings,path,synthetic=True))
    path.write_text(json.dumps(synthetic_document()),encoding='utf-8')
    with pytest.raises(ValueError,match='stay in simulation'):
        asyncio.run(execute_closed_loop(workspace,settings,path,synthetic=True,output_root=tmp/'data'))
    with pytest.raises(ValueError,match='only permitted'):
        asyncio.run(execute_closed_loop(workspace,settings,path,output_root=tmp/'other'))


def test_total_budget_does_not_call_provider_after_exhaustion(environment,monkeypatch):
    from memoria.llm.types import ChatResult
    _,settings,_=environment
    seen=[]
    class Provider:
        def __init__(self,settings):pass
        async def chat(self,messages,**kwargs):seen.append(messages);return ChatResult(content='{}')
    monkeypatch.setattr(simulation,'LLMClient',Provider)
    budget=simulation.TotalBudget(1,10000);a=budget.client(settings);b=budget.client(settings)
    asyncio.run(a.chat([{'role':'user','content':'engineering'}]))
    with pytest.raises(simulation.CallCeiling):
        asyncio.run(b.chat([{'role':'user','content':'engineering'}]))
    assert len(seen)==budget.calls==budget.blocked==1


def test_functional_assertions_reject_unknown_operators():
    with pytest.raises(ValueError,match='Unknown synthetic assertion'):
        simulation.check_observation({}, {'python_expression':'not executable'})


@pytest.mark.parametrize('change',[
    {'acceptance_scope':'partial_diagnostic'}, {'core_unchanged':False},
    {'question_outcomes':{'passed':1,'capability_gap':1}},
    {'case_execution_errors':1}, {'blocked_calls':1}, {'questions':0},
])
def test_full_acceptance_rejects_incomplete_or_different_code(change):
    result={'acceptance_scope':'complete_current_suite','core_unchanged':True,'questions':2,
        'question_outcomes':{'passed':2},'case_execution_errors':0,'blocked_calls':0}
    assert simulation.acceptance_passed(result)
    result.update(change)
    assert not simulation.acceptance_passed(result)


def test_full_synthetic_suite_uses_isolated_root_and_posthoc_assertions(environment,monkeypatch):
    workspace,settings,tmp=environment;settings.root=tmp
    folder=tmp/'simulation';folder.mkdir()
    payload=synthetic_document();payload['events']=payload['events'][:3]
    case={'id':'engineering_case','title':'temporary engineering','areas':['view'],'input':payload}
    (folder/'scenarios.json').write_text(json.dumps({'schema_version':1,'data_kind':'synthetic_functional','cases':[case]}),encoding='utf-8')
    (folder/'expectations.json').write_text(json.dumps({'cases':{'engineering_case':{
        'q1':{'capability':{'status_in':['answer'],'min_active_claims':1,'claim_predicate':'response_language','min_citations':1}},
        'q2':{'capability':{'path_in':['view'],'status_in':['answer']}}}}}),encoding='utf-8')
    monkeypatch.setattr(simulation,'LLMClient',Model)
    monkeypatch.setattr(simulation,'code_identity',lambda root:{'engineering_fixture':True})
    monkeypatch.setattr(records,'code_identity',lambda root:{'engineering_fixture':True})
    validation=asyncio.run(simulation.run_suite(workspace,settings))
    assert validation['model_calls']==0 and not (folder/'runs').exists()
    result=asyncio.run(simulation.run_suite(workspace,settings,execute=True,allow_remote=True))
    assert result['question_outcomes']=={'passed':2} and result['model_calls']==4
    output=Path(result['directory']);assert output.is_relative_to(folder/'runs')
    assert not workspace.path('experiments').exists()
    assert not workspace.path('memory','memoria.db').exists()
    assert json.loads((output/'manifest.json').read_text())['data_kind']=='synthetic_functional'
    run=next(output.glob('engineering_case/*/manifest.json'))
    assert json.loads(run.read_text())['protocol']['mode']=='synthetic_functional_v1'
    summary=simulation.summarize_runs(tmp)
    assert summary['question_outcomes']=={'passed':2} and summary['total_model_calls']==4
    assert simulation.summarize_runs(tmp)['total_model_calls']==4
    assert (Path(summary['directory'])/'report.html').exists()
    async def fatal(*args,**kwargs):raise RuntimeError('Engineering-only fatal setup')
    monkeypatch.setattr(simulation,'execute_closed_loop',fatal)
    failed=asyncio.run(simulation.run_suite(workspace,settings,execute=True,allow_remote=True))
    assert failed['questions']==2 and failed['question_outcomes']=={'execution_error':2}
    assert simulation.summarize_runs(tmp)['question_outcomes']=={'execution_error':2}
