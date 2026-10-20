"""Temporary engineering sources and model substitutes; never research gold or real user history."""
import asyncio
import copy
import json

import pytest
from pydantic import ValidationError

from memoria.config import Settings
from memoria.llm.types import ChatResult
from memoria.prompting.reader_budget import ReaderBudget
from memoria.prompting.assembler import _parse_frame
from memoria.memory.formation import OBSERVE_PROMPT
from memoria.memory.auto_review import PROMPT
from memoria.memory.source_formation import OBSERVE,VERIFY
from memoria.experiments.history import HistoryWorkspace
from memoria.experiments.production_replay import (ClosedLoopInput,execute_closed_loop,
    attribute_closed_loop,attribute_closed_loop_file,prepare_locomo_probe)
from tests.test_memory_kernel import config_at


class Model:
    def __init__(self,settings):
        self.settings=settings;self.reader_budget=ReaderBudget(settings)

    async def chat(self,messages,**kwargs):
        system=messages[0]['content'];text=messages[1]['content']
        if system==OBSERVE_PROMPT:
            source=json.loads(text)['source']
            value={'observations':[{'category':'preference','topic':'回答语言','scope':'','polarity':'positive',
                'content':'用户通常用中文回答。','quote':source,'asserted':True,'durable':True,'importance':3}]}
        elif system==PROMPT:
            data=json.loads(text)
            value={'decision':'accept','assessment':'Supported','durable':True,'self_related':True,
                'conditions_preserved':True,'time_supported':True,'sensitive':False,'evidence_quote':data['source'],
                'claim':{'predicate':'response_language','value':'中文','polarity':'positive'}}
        elif system==OBSERVE:
            data=json.loads(text);source=data['source']
            value={'observations':[{'category':'knowledge','topic':'部署','subject':'Memoria','conditions':'',
                'polarity':'positive','epistemic':'asserted','content':'据该资料，Memoria需核对配置。','quote':source,
                'feedback_quote':data['feedback']['content'] if data['feedback'] else '',
                'applicability':'仅限该工程资料'}]}
        elif system==VERIFY:
            value={'verdicts':[{'id':c['id'],'assessment':'Supported','attribution_preserved':True,
                'conditions_preserved':True,'task_bound':True,'sensitive':False} for c in json.loads(text)['candidates']]}
        else:
            _,sections=_parse_frame(text)
            section=next(s for s in sections if s.name in {'Structured State','Source Evidence'})
            packet=json.loads(section.content.rsplit('\n',1)[-1])
            ids=packet.get('allowed_evidence_ids') or [e['citation'] for e in packet['evidence']]
            value={'status':'answer','answer':'中文' if section.name=='Structured State' else '需核对配置。','evidence_ids':ids}
        return ChatResult(content=json.dumps(value,ensure_ascii=False),usage={'total_tokens':23},finish_reason='stop')


@pytest.fixture
def environment(tmp_path):
    settings=Settings.load(config_at(tmp_path/'config'))
    settings.data_root=tmp_path/'data'
    directory=settings.data_root/'users'/('a'*32)
    directory.mkdir(parents=True)
    return HistoryWorkspace(directory),settings,tmp_path


def document():
    return {'schema_version':1,'provenance':'Engineering-only temporary input, not empirical data',
        'allowed_purposes':['memory_processing','evaluation'],'subject_key':'engineering_subject',
        'events':[{'id':'m1','kind':'message','session':'s1','actor':'engineering_subject','role':'user','content':'我通常用中文回答。'},
            {'id':'q1','kind':'question','target':'personal','request':{'query':'我的回答语言是什么？'}},
            {'id':'q2','kind':'question','target':'personal','request':{'query':'我的回答语言是什么？'}},
            {'id':'m2','kind':'message','session':'s2','actor':'engineering_subject','role':'user','content':'我通常用中文回答。'}]}


def run(environment,payload=None,**kwargs):
    workspace,settings,tmp=environment
    path=tmp/'input.json';path.write_text(json.dumps(payload or document(),ensure_ascii=False),encoding='utf-8')
    return asyncio.run(execute_closed_loop(workspace,settings,path,execute=True,allow_remote=True,
        client_factory=Model,**kwargs))


def cases(path):return [json.loads(line) for line in (path/'cases.jsonl').read_text(encoding='utf-8').splitlines()]


def test_actual_worker_publishes_claim_and_reader_uses_view_before_future_sources(environment):
    path=run(environment);rows=cases(path)
    first=rows[0];trace=first['result']['closed_loop']
    assert first['status']=='completed' and first['result']['answer']=='中文'
    future=json.loads((path/'source-map.json').read_text())['m2']['id']
    assert set(first['prefix'])=={'m1'} and future not in json.dumps(first)
    assert trace['route']=='retrieve' and trace['selected'][0]['claim_revisions']
    assert first['result']['access']['state']['compiled_view_id']
    lineage=first['prefix']['m1']['lineage']
    assert lineage['observations'] and lineage['reviews'][0]['assessment']['decision']=='accept'
    assert any(p['kind']=='claim' and p['current'] and p['status']=='approved' for p in lineage['published'])
    current=next(p for p in lineage['published'] if p['kind']=='claim' and p['current'])
    assert current['admission_origin']=='llm'
    assert lineage['reviews'][0]['assessment']['applied_claim_revision_id']==current['id']
    assert trace['reader']['citations'] and trace['reader']['usage']['total_tokens']==23
    assert trace['reader']['validation']['semantic_entailment_verified'] is False
    assert rows[1]['result']['access']['path']=='view'
    assert not (environment[0].directory/'memory/memoria.db').exists()
    assert json.loads((path/'metrics.json').read_text())['production_writes']==0
    metrics=json.loads((path/'metrics.json').read_text())
    assert metrics['structured_state_attempts']==2
    assert metrics['claim_view_questions']==2
    assert metrics['compiled_view_questions']==1 and metrics['view_hit_questions']==1


def test_state_lookup_without_published_claim_is_not_counted_as_claim_view(environment):
    class OrdinaryModel(Model):
        async def chat(self,messages,**kwargs):
            result=await super().chat(messages,**kwargs)
            if messages[0]['content']==PROMPT:
                verdict=json.loads(result.content);verdict['claim']=None
                result.content=json.dumps(verdict,ensure_ascii=False)
            return result
    workspace,settings,tmp=environment
    payload=document();payload['events']=payload['events'][:3]
    source=tmp/'ordinary-input.json';source.write_text(json.dumps(payload),encoding='utf-8')
    path=asyncio.run(execute_closed_loop(workspace,settings,source,execute=True,allow_remote=True,client_factory=OrdinaryModel))
    metrics=json.loads((path/'metrics.json').read_text())
    assert metrics['structured_state_attempts']==2
    assert metrics['claim_view_questions']==metrics['compiled_view_questions']==metrics['view_hit_questions']==0
    assert any(p['kind']=='memory' for p in cases(path)[0]['prefix']['m1']['lineage']['published'])


def test_posthoc_correct_nonanswer_is_not_presented_as_reader_failure(environment):
    payload=document();payload['events']=payload['events'][:2]+[
        {'id':'withdraw','kind':'retract','source':'m1'},payload['events'][2]]
    row=cases(run(environment,payload))[1]
    diagnostic=attribute_closed_loop(row,{'case_id':'q2','stage_judgments':{'answer':True}})
    assert diagnostic['stage']=='declared_correct_nonanswer'
    assert diagnostic['unknown_semantic_stages']==['extraction','publication','retrieval','selection','reader']
    assert diagnostic['declared_answer_correct'] is True
    unknown=attribute_closed_loop(row,{'case_id':'q2'})
    assert unknown['stage']=='reader_not_called' and unknown['declared_answer_correct'] is None


def test_probe_rejects_unverified_dataset_without_materializing_input(environment):
    workspace,_,tmp=environment
    source=tmp/'unverified.json';source.write_text('[]',encoding='utf-8')
    with pytest.raises(ValueError,match='fingerprint'):
        prepare_locomo_probe(workspace,source)
    assert not workspace.path('research','benchmarks').exists()


@pytest.mark.parametrize('version',['v1','v2'])
def test_fixed_probe_preserves_exact_text_separates_identity_and_excludes_gold(environment,monkeypatch,version):
    from memoria.experiments import records,locomo
    workspace,_,tmp=environment
    # Engineering-only source and fingerprint substitute; never published as actual benchmark data.
    text='Engineering fixture, not a real user or benchmark history.'
    raw={'sample_id':'engineering-conv','qa':[{'answer':'GOLD_CANARY'}],
        'conversation':{'session_1_date_time':'RAW_DATE_ONLY','session_15_date_time':'RAW_DATE_ONLY',
            'session_1':[{'dia_id':'D1:16','speaker':'Melanie','text':text,'blip_caption':'IMAGE_CANARY'}],
            'session_15':[{'dia_id':'D15:12','speaker':'Melanie','text':text,'blip_caption':'IMAGE_CANARY'}]}}
    source=tmp/'engineering.json';source.write_text(json.dumps([raw]),encoding='utf-8')
    digest=records.digest_file
    monkeypatch.setattr(records,'digest_file',lambda p:locomo.SOURCE_SHA256 if str(p)==str(source) else digest(p))
    path=prepare_locomo_probe(workspace,source,version=version)
    document=ClosedLoopInput.model_validate_json(path.read_text(encoding='utf-8'))
    assert document.subject_key=='engineering-conv:Melanie' and document.subject_key!=workspace.owner
    assert document.events[0].content==text and document.events[0].original_time is None
    assert len([e for e in document.events if e.kind=='message'])==1
    assert len([e for e in document.events if e.kind=='question'])==5
    assert document.events[-2].kind=='retract'
    bundle=''.join(p.read_text(encoding='utf-8') for p in path.parent.iterdir())
    assert 'GOLD_CANARY' not in bundle and 'IMAGE_CANARY' not in bundle
    assert 'RAW_DATE_ONLY' in bundle
    assert prepare_locomo_probe(workspace,source,version=version)==path
    path.write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='never overwrite'):
        prepare_locomo_probe(workspace,source,version=version)


@pytest.mark.parametrize('fault',['speaker','duplicate'])
def test_probe_does_not_silently_rebind_or_choose_a_duplicate_turn(environment,monkeypatch,fault):
    from memoria.experiments import records,locomo
    workspace,_,tmp=environment
    turn={'dia_id':'D15:12','speaker':'someone_else' if fault=='speaker' else 'Melanie','text':'Engineering only'}
    raw=[{'sample_id':'engineering-conv','conversation':{'session_15':[turn]* (2 if fault=='duplicate' else 1)}}]
    source=tmp/'engineering.json';source.write_text(json.dumps(raw),encoding='utf-8')
    digest=records.digest_file
    monkeypatch.setattr(records,'digest_file',lambda p:locomo.SOURCE_SHA256 if str(p)==str(source) else digest(p))
    with pytest.raises(ValueError,match='identity or turn'):
        prepare_locomo_probe(workspace,source)


def test_document_formation_and_production_source_reader_do_not_create_personal_claim(environment):
    payload=document();payload['subject_key']=None
    payload['events']=[{'id':'d1','kind':'source','body':{'kind':'document','title':'工程资料',
        'content':'Memoria部署需核对配置。','authorship':'external'}},
        {'id':'q1','kind':'question','target':'knowledge','request':{'query':'Memoria部署','domains':['library']}}]
    path=run(environment,payload);row=cases(path)[0]
    assert row['status']=='completed' and row['result']['status']=='answer'
    assert row['result']['closed_loop']['selected'][0]['kind']=='source_quote'
    lineage=row['prefix']['d1']['lineage']
    assert lineage['observations'][0]['status']=='approved'
    assert all(p['kind']!='claim' for p in lineage['published'])
    assert row['result']['citations'][0]['provenance']['authorship']=='external'


def test_task_feedback_then_explicit_form_uses_the_same_source_pipeline(environment):
    payload=document();payload['subject_key']=None
    payload['events']=[{'id':'d1','kind':'source','form':False,'body':{'kind':'task_result','title':'工程任务',
        'content':'Memoria部署需核对配置。','authorship':'external'}},
        {'id':'f1','kind':'feedback','source':'d1','outcome':'success','content':'工程检查通过。'},
        {'id':'form1','kind':'form','source':'d1'},
        {'id':'q1','kind':'question','target':'knowledge','request':{'query':'Memoria部署','domains':['library']}}]
    row=cases(run(environment,payload))[0]
    assert row['prefix']['d1']['lineage']['observations'][0]['status']=='approved'
    assert row['result']['status']=='answer'
    assert row['result']['access']['evidence'][0]['provenance']['knowledge_cards'][0]['feedback_quote']=='工程检查通过。'


def test_retraction_stops_old_claim_view_before_reader_and_keeps_history(environment):
    payload=document();payload['events']=payload['events'][:2]+[
        {'id':'undo','kind':'retract','source':'m1'},payload['events'][2]]
    rows=cases(run(environment,payload))
    assert rows[0]['result']['status']=='answer'
    assert not rows[1]['prefix']['m1']['lineage']['available']
    assert rows[1]['result']['status']=='clarify' and rows[1]['result']['reader_called'] is False
    assert rows[1]['prefix']['m1']['lineage']['published']  # Audit versions remain; they are not reusable evidence.


@pytest.mark.parametrize('fault',['invented_citation','incomplete','revoked','malformed_json'])
def test_source_reader_rejects_unseen_citations_incomplete_output_and_inflight_retraction(environment,fault,monkeypatch):
    from memoria.memory.kernel import MemoryKernel
    original=MemoryKernel.__init__
    live={}
    def remember(self,*args,**kwargs):original(self,*args,**kwargs);live['kernel']=self
    monkeypatch.setattr(MemoryKernel,'__init__',remember)
    class FaultModel(Model):
        async def chat(self,messages,**kwargs):
            result=await super().chat(messages,**kwargs)
            if messages[0]['content'].startswith('这是来源资料阅读'):
                assert kwargs['temperature']==0 and kwargs['top_p']==1
                if fault=='invented_citation':result.content=json.dumps({'status':'answer','answer':'bad','evidence_ids':['[library:invented]']})
                elif fault=='incomplete':result.finish_reason='length'
                elif fault=='malformed_json':result.content='{"status":"answer","answer":"bad",evidence_ids":[]}'
                else:
                    kernel=live['kernel'];sid=kernel.library.list()[0]['id'];kernel.library.retract(sid)
            return result
    payload=document();payload['events']=[{'id':'d1','kind':'source','form':False,
        'body':{'kind':'document','title':'工程资料','content':'Memoria部署需核对配置。'}},
        {'id':'q1','kind':'question','target':'knowledge','request':{'query':'Memoria部署','domains':['library']}}]
    workspace,settings,tmp=environment;source=tmp/'input.json';source.write_text(json.dumps(payload),encoding='utf-8')
    path=asyncio.run(execute_closed_loop(workspace,settings,source,execute=True,allow_remote=True,client_factory=FaultModel))
    result=cases(path)[0]['result']
    assert result['status']=='abstain' and result['citations']==[] and not result['validation']['valid']


def test_budget_failures_and_unexecuted_questions_remain_in_original_denominator(environment):
    path=run(environment,max_calls=1);rows=cases(path)
    metrics=json.loads((path/'metrics.json').read_text())
    assert len(rows)==2 and all(r['status']=='error' for r in rows)
    assert metrics['model_calls']==1 and metrics['blocked_call_attempts']>=1
    assert metrics['errors']>=2 and metrics['questions']==2


@pytest.mark.parametrize('fault',['actor','gold','route','future','duplicate'])
def test_oracle_fields_identity_guessing_and_future_references_rejected(fault):
    value=document()
    if fault=='actor':value['events'][0]['actor']='someone_else'
    if fault=='gold':value['events'][1]['answer']='中文'
    if fault=='route':value['events'][1]['request']['predicates']=['response_language']
    if fault=='future':value['events'].insert(0,{'id':'r','kind':'retract','source':'m1'})
    if fault=='duplicate':value['events'][-1]['id']='m1'
    with pytest.raises(ValidationError):ClosedLoopInput.model_validate(value)


def test_post_hoc_attribution_keeps_unknown_semantics_and_cannot_read_future_or_mutate_run(environment):
    path=run(environment);row=cases(path)[0]
    base={'case_id':'q1','support_groups':[['m1']],'required_route':'claim'}
    report=attribute_closed_loop(row,base)
    assert report['stage']=='answered_unscored' and report['semantic_sufficiency'] is None
    failed=copy.deepcopy(row);failed['result']['closed_loop']['selected']=[]
    assert attribute_closed_loop(failed,base)['stage']=='candidate_not_selected'
    failed['result']['closed_loop']['candidates']=[]
    assert attribute_closed_loop(failed,base)['stage']=='not_retrieved'
    assert attribute_closed_loop(row,base|{'stage_judgments':{'extraction':False}})['stage']=='extraction'
    with pytest.raises(ValueError,match='future'):attribute_closed_loop(row,base|{'support_groups':[['m2']]})
    original=(path/'cases.jsonl').read_bytes()
    labels=environment[2]/'labels.json'
    labels.write_text(json.dumps({'reviewer':'engineering_only','provenance':'temporary test declarations',
        'cases':[base,base|{'case_id':'q2'}]}),encoding='utf-8')
    output=attribute_closed_loop_file(environment[0],environment[1],path.name,labels)
    assert (path/'cases.jsonl').read_bytes()==original
    assert json.loads((output/'metrics.json').read_text())['model_calls']==0
    (path/'source-map.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='fingerprint'):attribute_closed_loop_file(environment[0],environment[1],path.name,labels)


def test_validation_only_makes_no_run_no_database_and_no_model(environment):
    workspace,settings,tmp=environment
    source=tmp/'input.json';source.write_text(json.dumps(document()),encoding='utf-8')
    result=asyncio.run(execute_closed_loop(workspace,settings,source))
    assert result['status']=='validated_not_executed' and result['model_calls']==0
    assert not (workspace.directory/'experiments').exists()


@pytest.mark.parametrize('stage',[OBSERVE_PROMPT,PROMPT],ids=['extract','review'])
def test_valid_json_with_length_finish_reason_never_publishes_a_claim(environment,stage):
    class IncompleteModel(Model):
        async def chat(self,messages,**kwargs):
            result=await super().chat(messages,**kwargs)
            if messages[0]['content']==stage:result.finish_reason='length'
            return result
    workspace,settings,tmp=environment;payload=document();payload['events']=payload['events'][:2]
    source=tmp/'input.json';source.write_text(json.dumps(payload),encoding='utf-8')
    path=asyncio.run(execute_closed_loop(workspace,settings,source,execute=True,allow_remote=True,client_factory=IncompleteModel))
    row=cases(path)[0]
    assert not row['prefix']['m1']['lineage']['published']
    if stage==PROMPT:
        assert row['prefix']['m1']['lineage']['reviews'][0]['assessment']['reason_codes']==['review_incomplete_output']
    else:assert row['status']=='error'


def test_new_answer_and_lineage_routes_require_gateway_and_keep_tenants_separate(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from unittest.mock import AsyncMock
    from memoria.api import create_app
    from memoria.service import AgentService
    from memoria.llm import LLMClient
    monkeypatch.setattr(AgentService,'start_integrations',AsyncMock())
    monkeypatch.setattr(LLMClient,'chat',Model.chat)
    app=create_app(config_at(tmp_path))
    with TestClient(app) as client:
        headers=[]
        for name in ('loop_a','loop_b'):
            credentials={'username':name,'password':'Engineering-only-passphrase-2026'}
            assert client.post('/api/auth/register',json=credentials).status_code==201
            token=client.post('/api/auth/login',json=credentials).json()['access_token']
            headers.append({'Authorization':'Bearer '+token})
        a,b=headers
        source=client.post('/api/memory-kernel/sources',headers=a,json={
            'kind':'document','title':'工程资料','content':'Memoria部署需核对配置。','authorship':'external'}).json()
        route='/api/memory-kernel/lineage/library/'+source['id']
        assert client.get(route,headers=a).json()['available']
        assert not client.get(route,headers=b).json()['available']
        response=client.post('/api/memory-kernel/knowledge-answer',headers=a,json={'query':'Memoria部署'})
        assert response.status_code==200 and response.json()['status']=='answer'
        assert client.post('/api/memory-kernel/knowledge-answer',headers=b,json={'query':'Memoria部署'}).json()['reader_called'] is False
        assert client.post('/api/memory-kernel/knowledge-answer',headers=a,json={'query':'x','domains':['memory']}).status_code==422
        assert client.post('/api/memory-kernel/knowledge-answer',headers=a,json={'query':'x','user_id':'forged'}).status_code==422
        client.cookies.clear()
        assert client.get(route).status_code==401
        assert client.post('/api/memory-kernel/knowledge-answer',json={'query':'Memoria'}).status_code==401


@pytest.mark.parametrize('failed',[False,True],ids=['knowledge_tool','provider_failure'])
def test_runtime_records_actual_tool_visibility_or_unknown_failed_usage(tmp_path,failed):
    from memoria.api.app import _create_tenant_app
    from memoria.llm.types import ProviderError
    from memoria.memory.library import SourceInput
    from memoria.observability.memory_trace import fingerprint
    app=_create_tenant_app(config_at(tmp_path));service=app.state.service;store=app.state.store
    sid=store.create_session('Engineering audit')['id'];service.runtime.retrieval_planner.planner=None
    service.kernel.ingest(SourceInput(kind='document',title='工程来源',content='Memoria工程部署需核对配置。'))
    count=0
    async def chat(messages,**kwargs):
        nonlocal count
        count+=1
        if failed:raise ProviderError('Temporary engineering fault')
        if count==1:
            arguments={'name':'search_knowledge','arguments':{'query':'Memoria工程部署','domains':['library']}}
            return ChatResult(tool_calls=[{'id':'t','name':'tool_call','arguments':arguments}],raw_message={
                'content':None,'tool_calls':[{'id':'t','type':'function','function':{'name':'tool_call','arguments':json.dumps(arguments)}}]})
        return ChatResult(content='据工程资料，部署需核对配置。',usage={'total_tokens':17})
    service.llm.chat=chat
    try:
        if failed:
            with pytest.raises(ProviderError):asyncio.run(service.runtime.run(sid,'你好',skip_memory_enqueue=True))
            trace=store.traces(sid)[0]
            assert trace['metadata']['llm_calls'][0]['usage'] is None
            assert trace['metadata']['llm_calls'][0]['status']=='failed'
            assert trace['metadata']['memory_closed_loop']['reader']['status']=='execution_failure'
        else:
            _,_,trace=asyncio.run(service.runtime.run(sid,'请查看工程资料',skip_memory_enqueue=True))
            metadata=trace['metadata'];proof=metadata['memory_closed_loop']
            assert proof['selected'] and proof['reader']['validation']['valid'] is None
            assert any(e['literal_content_present'] for read in metadata['llm_calls'][-1]['memory_read_visibility'] for e in read['evidence'])
            assert proof['trace_sha256']==fingerprint({k:v for k,v in proof.items() if k!='trace_sha256'})
        assert 'memory_closed_loop_error' not in trace['metadata']
    finally:store.close()
