"""Isolated engineering cases, not real history, gold labels or model-quality scores."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from memoria.config import Settings,ModelConfig
from memoria.claims import ClaimDraft,ClaimDecision,ClaimRevision,sha
from memoria.api.app import _create_tenant_app
from memoria.api import create_app
from fastapi.testclient import TestClient
from memoria.llm import LLMClient,ChatResult,ContextLengthError
from memoria.prompting import PromptAssembler,ContextBudget
from memoria.prompting.reader_budget import ReaderBudget
from memoria.memory.reader import CitationGuard,SAFE_REJECTED,SAFE_UNKNOWN,read_memory_answer
from memoria.memory.access import MemoryAccessOrchestrator,QueryRequest
from memoria.prompting.assembler import _parse_frame
from memoria.lifecycle import Phase


@pytest.fixture
def core(tmp_path):
    cfg=tmp_path/'config.toml'
    cfg.write_text(f'[storage]\ndatabase="{(tmp_path/"fixture.db").as_posix()}"\n[llm.main]\nmodel="fixture"\napi_key="x"\nbase_url="http://example.test/v1"\n[agent.mcp]\nenabled=false\n[memory.markdown]\nenabled=false\n')
    app=_create_tenant_app(cfg); service=app.state.service; store=app.state.store
    session=store.create_session('Temporary engineering only')['id']
    yield store,service,session,cfg
    store.close()


def approved(core,value='中文',predicate='response_language',**kw):
    store,service,sid,_=core
    text='临时工程原话：'+value
    msg=store.add_message(sid,'user',text)
    evidence=dict(message_id=msg['id'],start=0,end=len(text),quote=text,content_sha256=sha(text))
    claim=service.claims.create(ClaimDraft(value=value,predicate=predicate,evidence=[evidence],**kw))
    return service.claims.approve(claim['id'],ClaimDecision(expected_revision=1,reason='Explicit test approval'))


def guard_for(core,query='我用什么语言回答',**kw):
    request=QueryRequest(query=query,**kw)
    service=core[1]
    access=asyncio.run(MemoryAccessOrchestrator(service.claims,service.runtime.memory).access(request))
    guard=CitationGuard(service.claims,access,request)
    messages=PromptAssembler().assemble('rules',extra_sections=[guard.section()]).as_messages()+[dict(role='user',content=query)]
    return guard,messages


def valid_response(guard):
    return ChatResult(content=json.dumps({'status':'answer','answer':'临时工程回答','evidence_ids':[k for k,v in guard.catalog.items() if v['eligible']]},ensure_ascii=False))


@pytest.mark.parametrize('reject_at',[None,'gate','support','revoked','provider'])
def test_opt_in_quoted_risk_keeps_approval_freshness_and_no_rejected_answer(core,reject_at):
    claim=approved(core)
    guard,_=guard_for(core)
    ids=[k for k,v in guard.catalog.items() if v['eligible']]
    class RiskLLM:
        settings=Settings.load(core[3])
        calls=0
        async def chat(self,messages,**kwargs):
            self.calls+=1
            if messages[0]['content'].startswith('Assess'):
                packet=json.loads(messages[1]['content']);source=packet['evidence'][0]
                missing=reject_at=='gate'
                value={'state':'missing_fact' if missing else 'sufficient','requirements':[{'fact':'回答语言',
                    'status':'missing' if missing else 'supported','sources':[] if missing else
                    [{'id':source['id'],'quote':source['text']}]}],'missing_queries':[],'reason':'工程临时判断'}
            elif messages[0]['content'].startswith('Verify'):
                packet=json.loads(messages[1]['content']);source=packet['evidence'][0]
                supported=reject_at!='support'
                value={'verdict':'supported' if supported else 'insufficient','claims':[{'statement':'中文',
                    'relation':'supported' if supported else 'insufficient','basis':'explicit',
                    'sources':[{'id':source['id'],'quote':source['text']}],'reason':'工程临时判断'}],
                    'missing_queries':[],'reason':'工程临时判断'}
                if reject_at=='revoked':
                    core[1].claims.decide(claim['id'],ClaimDecision(expected_revision=claim['revision'],reason='Test revocation'),'retract')
            else:
                if reject_at=='provider': raise RuntimeError('Temporary provider failure')
                value={'status':'answer','answer':'中文','evidence_ids':ids}
            return ChatResult(content=json.dumps(value,ensure_ascii=False),usage={'total_tokens':10})
    llm=RiskLLM()
    result=asyncio.run(read_memory_answer(core[1].claims,core[1].runtime.memory,llm,
        QueryRequest(query='我用什么语言回答',use_answer_risk=True)))
    assert llm.calls==(1 if reject_at=='gate' else 2 if reject_at=='provider' else 3)
    assert result['status']==('answer' if reject_at is None else 'abstain')
    assert result['validation']['semantic_entailment_verified'] is False
    if reject_at is not None:
        assert result['citations']==[] and result['answer'] in {SAFE_UNKNOWN,SAFE_REJECTED}
    assert 'risk_checks' in result


def settings(**kw):
    return SimpleNamespace(main=ModelConfig('fixture','x','http://example.test/v1'),max_tokens=32,
                           reader_input_bytes=4096,reader_context_tokens=None,reader_safety_tokens=8,**kw)


def test_byte_budget_matches_actual_http_body_including_tools_and_options(core):
    s=core[1].settings
    s.main=ModelConfig('fixture','x','https://api.deepseek.com/v1','disabled')
    bodies=[]
    def handler(request):
        bodies.append(request.content)
        return httpx.Response(200,json={'choices':[{'message':{'content':'okay'},'finish_reason':'stop'}]})
    client=LLMClient(s,transport=httpx.MockTransport(handler))
    tools=[{'type':'function','function':{'name':'fixture','description':'中文工具定义'*15,'parameters':{'type':'object'}}}]
    messages=[{'role':'system','content':'rules'},{'role':'user','content':'完整用户输入'}]
    result=asyncio.run(client.chat(messages,tools=tools,max_tokens=17))
    assert result.budget['input_bytes']==len(bodies[0])
    assert json.loads(bodies[0])['thinking']=={'type':'disabled'}
    assert result.budget['reserved_output_tokens']==17 and result.budget['input_tokens'] is None
    s.reader_input_bytes=256
    with pytest.raises(ContextLengthError): asyncio.run(client.chat(messages,tools=tools,max_tokens=17))
    assert len(bodies)==1


def test_streaming_payload_is_counted_before_delivery(core):
    s=core[1].settings; bodies=[]; deltas=[]
    def handler(request):
        bodies.append(request.content)
        return httpx.Response(200,text='data: {"choices":[{"delta":{"content":"okay"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',headers={'Content-Type':'text/event-stream'})
    async def delta(text): deltas.append(text)
    result=asyncio.run(LLMClient(s,httpx.MockTransport(handler)).chat_stream([dict(role='user',content='工程输入')],on_delta=delta,max_tokens=8))
    assert result.budget['input_bytes']==len(bodies[0]) and json.loads(bodies[0])['stream_options']['include_usage']
    assert deltas==['okay']


def test_local_full_template_budget_reserves_output_and_safety():
    s=settings();s.main=ModelConfig('fixture','','local://cpu');s.reader_context_tokens=80
    meter=ReaderBudget(s,token_counter=lambda model,messages,tools:(40+(20 if tools else 0),100))
    report=asyncio.run(meter.check(s.main,[dict(role='user',content='工程')],None,32))
    assert report['input_tokens']+report['reserved_output_tokens']+report['safety_tokens']==80
    assert report['exact_input_tokens'] and report['provider_window_verified']
    with pytest.raises(ContextLengthError): asyncio.run(meter.check(s.main,[dict(role='user',content='工程')],[{'schema':'tools'}],32))


def test_remote_exact_window_cannot_silently_fall_back_to_characters():
    s=settings();s.reader_context_tokens=8192
    with pytest.raises(ContextLengthError): asyncio.run(ReaderBudget(s).prepare([dict(role='user',content='test')]))


@pytest.mark.parametrize('bad',['model','protocol','count','redirect'])
def test_local_http_counter_binding_failure_stops_generation(core,bad):
    s=core[1].settings;s.main=ModelConfig('fixture','','http://127.0.0.1:8080/v1');s.reader_local_count=True
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        payload={'protocol':'memoria-chat-template-v1','model':'fixture','input_tokens':12,'context_tokens':8192,'tokenizer_sha256':'0'*64}
        if bad=='model':payload['model']='other'
        if bad=='protocol':payload['protocol']='unverified'
        if bad=='count':payload['input_tokens']=True
        return httpx.Response(302,headers={'Location':'http://external.invalid/'}) if bad=='redirect' else httpx.Response(200,json=payload)
    with pytest.raises(ContextLengthError):
        asyncio.run(LLMClient(s,httpx.MockTransport(handler)).chat([dict(role='user',content='engineering')]))
    assert calls==['/v1/reader-token-count']


def test_local_http_full_token_counter_then_generation_are_bound(core):
    s=core[1].settings;s.main=ModelConfig('fixture','','http://127.0.0.1:8080/v1');s.reader_local_count=True
    bodies=[]
    def handler(request):
        bodies.append(json.loads(request.content))
        if request.url.path.endswith('reader-token-count'):
            return httpx.Response(200,json={'protocol':'memoria-chat-template-v1','model':'fixture','input_tokens':25,'context_tokens':8192,'tokenizer_sha256':'0'*64})
        return httpx.Response(200,json={'choices':[{'message':{'content':'okay'},'finish_reason':'stop'}]})
    result=asyncio.run(LLMClient(s,httpx.MockTransport(handler)).chat([dict(role='user',content='工程')],max_tokens=17))
    assert bodies[0]==bodies[1]
    assert result.budget['input_tokens']==25 and result.budget['token_count_origin']=='local_http'


def test_local_count_endpoint_loads_no_model_weights(monkeypatch):
    from memoria.llm.server import create_local_openai_app
    calls=[]
    def count(path,messages,tools,cap):
        calls.append((messages,tools,cap));return 23,4096,'1'*64
    monkeypatch.setattr('memoria.prompting.reader_budget.count_local_path',count)
    app=create_local_openai_app(model='model/Qwen3.5-2B',device='cpu',max_context_tokens=4096)
    with TestClient(app) as client:
        response=client.post('/v1/reader-token-count',json={'model':'model/Qwen3.5-2B','messages':[{'role':'user','content':'test'}],'max_tokens':32})
        assert response.status_code==200 and response.json()['input_tokens']==23
        assert not client.get('/health').json()['loaded']
        assert client.post('/v1/reader-token-count',json={'model':'foreign-model','messages':[{'role':'user','content':'test'}]}).status_code==404
    assert len(calls)==1


def test_fit_preserves_current_query_and_whole_tool_exchange():
    s=settings();s.reader_input_bytes=256
    messages=[dict(role='system',content='rules'),dict(role='user',content='current'),
              dict(role='assistant',content=None,tool_calls=[{'id':'call','function':{'name':'fixture','arguments':'{}'}}]),
              dict(role='tool',tool_call_id='call',content='long result'*100)]
    result=asyncio.run(ReaderBudget(s).prepare(messages))
    assert result.report['dropped_messages']==2
    assert result.messages==messages[:2] and result.report['fits']
    with pytest.raises(ContextLengthError):
        asyncio.run(ReaderBudget(s).prepare([dict(role='system',content='rules'),dict(role='user',content='完整查询'*100)]))
    with pytest.raises(ContextLengthError):
        asyncio.run(ReaderBudget(s).prepare(messages[:2],[{'tool_schema':'x'*500}]))


def test_prepared_model_and_generation_are_frozen():
    s=settings();prepared=asyncio.run(ReaderBudget(s).prepare([dict(role='user',content='test')]))
    s.main.model='new-model';s.max_tokens=999
    assert prepared.model.model=='fixture' and prepared.max_tokens==32


def test_valid_citations_are_owner_source_bound_and_not_semantic_proof(core):
    claim=approved(core)
    guard,messages=guard_for(core)
    verdict=guard.validate(valid_response(guard),messages)
    assert verdict['status']=='answer' and verdict['validation']['valid']
    assert not verdict['validation']['semantic_entailment_verified']
    assert verdict['citations'][0]['source_spans'][0]['message_id']==claim['evidence'][0]['message_id']


@pytest.mark.parametrize('change',['foreign','duplicate','missing','prose','extra','duplicate_key','inline','tool_call'])
def test_invalid_or_unseen_citation_never_becomes_a_fact_answer(core,change):
    approved(core); guard,messages=guard_for(core)
    result=valid_response(guard);data=json.loads(result.content)
    if change=='foreign':data['evidence_ids']=['claim:another-owner:foreign']
    if change=='duplicate':data['evidence_ids']*=2
    if change=='missing':data['evidence_ids']=[]
    if change=='extra':data['extra']='forbidden'
    if change=='inline':data['answer']='see source:foreign:never-seen'
    result.content=json.dumps(data)
    if change=='prose':result.content='I remember unsupported facts'
    if change=='duplicate_key':result.content=result.content[:-1]+',"status":"answer"}'
    if change=='tool_call':result.tool_calls=[{'name':'memorize','arguments':{}}]
    verdict=guard.validate(result,messages)
    assert verdict['status']=='abstain' and verdict['answer']==SAFE_REJECTED and verdict['citations']==[]


def test_abstention_text_cannot_smuggle_unverified_facts(core):
    approved(core);guard,messages=guard_for(core)
    result=ChatResult(content=json.dumps({'status':'abstain','answer':'Maybe some unsupported personal fact','evidence_ids':[]}))
    assert guard.validate(result,messages)['answer']==SAFE_UNKNOWN


def test_complete_json_with_length_finish_cannot_pass_citation_guard(core):
    approved(core);guard,messages=guard_for(core)
    result=valid_response(guard);result.finish_reason='length'
    verdict=guard.validate(result,messages)
    assert verdict['status']=='abstain' and verdict['validation']['reason']=='incomplete_reader_output'


def test_opt_in_pipeline_bytes_are_reserved_before_reader_is_called(core):
    approved(core)
    class LLM:
        settings=Settings.load(core[3]);calls=0
        async def chat(self,messages,**kw):
            self.calls+=1
            assert messages[0]['content'].startswith('Assess')
            item=json.loads(messages[1]['content'])['evidence'][0]
            return ChatResult(content=json.dumps({'state':'sufficient','requirements':[{'fact':'language',
                'status':'supported','sources':[{'id':item['id'],'quote':item['text']}]}],
                'missing_queries':[],'reason':'Engineering only'}))
    llm=LLM();llm.settings.reader_input_bytes=12000
    result=asyncio.run(read_memory_answer(core[1].claims,core[1].runtime.memory,llm,
        QueryRequest(query='我用什么语言回答',use_answer_risk=True)))
    assert llm.calls==1 and not result['reader_called'] and result['citations']==[]
    assert result['validation']['reason']=='answer_pipeline_budget_unavailable'
    assert result['risk_checks']['policy']['calls']==1
    assert any(t['decision']=='pipeline_budget_unavailable' for t in result['risk_checks']['policy']['trace'])


def test_production_truncated_support_regenerates_and_preserves_both_usages(core):
    approved(core);guard,_=guard_for(core);ids=list(guard.catalog)
    class LLM:
        settings=Settings.load(core[3]);calls=0;support_calls=0
        async def chat(self,messages,**kw):
            self.calls+=1;packet=json.loads(messages[1]['content']) if messages[0]['content'].startswith(('Assess','Verify')) else {}
            if messages[0]['content'].startswith('Assess'):
                source=packet['evidence'][0]
                value={'state':'sufficient','requirements':[{'fact':'language','status':'supported',
                    'sources':[{'id':source['id'],'quote':source['text']}]}],'missing_queries':[],'reason':'Direct'}
            elif messages[0]['content'].startswith('Verify'):
                self.support_calls+=1
                if self.support_calls==1:return ChatResult(content='{"verdict":',finish_reason='length',usage={'total_tokens':20})
                source=packet['evidence'][0]
                value={'verdict':'supported','claims':[{'statement':'中文','relation':'supported','basis':'explicit',
                    'sources':[{'id':source['id'],'quote':source['text']}],'reason':'Direct'}],'missing_queries':[],'reason':'Direct'}
            else:value={'status':'answer','answer':'中文','evidence_ids':ids}
            return ChatResult(content=json.dumps(value),usage={'total_tokens':10})
    llm=LLM();result=asyncio.run(read_memory_answer(core[1].claims,core[1].runtime.memory,llm,
        QueryRequest(query='我用什么语言回答',use_answer_risk=True)))
    assert result['status']=='answer' and llm.calls==4
    assert sum(u['total_tokens'] for u in result['risk_checks']['usage'])==40
    assert result['risk_checks']['contract_errors'][0]['code']=='output_length'
    assert result['risk_checks']['policy']['reserved_calls']==0


def test_claim_conditions_and_negation_cannot_lose_required_citations(core):
    approved(core,'咖啡',predicate='diet'); approved(core,'茶',predicate='diet',polarity='negative')
    guard,messages=guard_for(core,'我的饮食有哪些')
    response=valid_response(guard);data=json.loads(response.content);data['evidence_ids']=data['evidence_ids'][:1]
    response.content=json.dumps(data)
    assert guard.validate(response,messages)['validation']['reason']=='incomplete_claim_citations'


def test_budget_omitted_frame_and_user_paste_cannot_authorize_citations(core):
    approved(core);guard,messages=guard_for(core)
    trimmed=PromptAssembler(max_frame_chars=250).assemble('rules',extra_sections=[guard.section()]).as_messages()+[messages[-1]]
    # A user message containing the exact packet is not the trusted context frame.
    trimmed.insert(2,dict(role='user',content=guard.packet))
    assert not guard.validate(valid_response(guard),trimmed)['validation']['valid']


@pytest.mark.parametrize('change',['source','revision','revocation'])
def test_awaited_answer_is_rejected_after_source_or_state_change(core,change):
    store,service,sid,_=core;claim=approved(core);guard,messages=guard_for(core)
    if change=='source':
        store.db.execute('UPDATE messages SET content=? WHERE id=?',('changed',claim['evidence'][0]['message_id']));store.db.commit()
    elif change=='revocation':
        service.claims.decide(claim['id'],ClaimDecision(expected_revision=claim['revision'],reason='Test withdrawal'),'retract')
    else:
        text='临时工程：英文';msg=store.add_message(sid,'user',text)
        revised=service.claims.revise(claim['id'],ClaimRevision(expected_revision=claim['revision'],value='英文',evidence=[dict(message_id=msg['id'],start=0,end=len(text),quote=text,content_sha256=sha(text))]))
        service.claims.approve(claim['id'],ClaimDecision(expected_revision=revised['revision'],reason='Test correction'))
    verdict=guard.validate(valid_response(guard),messages)
    assert not verdict['validation']['valid'] and verdict['answer']==SAFE_REJECTED


def test_historical_known_revision_can_be_cited_without_current_state_leak(core):
    claim=approved(core);guard,messages=guard_for(core,recorded_as_of=claim['recorded_at'])
    assert guard.validate(valid_response(guard),messages)['validation']['valid']


def test_message_insert_does_not_commit_callers_transaction(core):
    store,_,sid,_=core
    store.db.execute('BEGIN')
    item=store.add_message(sid,'user','temporary transaction test')
    assert store.db.in_transaction
    store.db.rollback()
    assert store.message_source(item['id']) is None


def test_runtime_buffers_model_and_rejects_before_streaming(core):
    store,service,sid,_=core;approved(core);events=[]
    async def reader(messages,**kwargs):
        assert kwargs['tools'] is None
        assert not any(e['type']=='delta' for e in events)
        return ChatResult(content='unvalidated provider text')
    async def forbidden(*args,**kwargs): raise AssertionError('strict reader never streams provider output')
    async def event(value):events.append(value)
    service.llm.chat=reader;service.llm.chat_stream=forbidden
    message,_,trace=asyncio.run(service.runtime.run(sid,'我用什么语言回答',on_event=event,skip_memory_enqueue=True))
    assert message['content']==SAFE_REJECTED
    assert [e['content'] for e in events if e['type']=='delta']==[SAFE_REJECTED]
    assert trace['metadata']['citation_validation']['reason']=='invalid_answer_schema'


def test_runtime_validated_stream_and_after_hook_cannot_replace_answer(core):
    _,service,sid,_=core;approved(core)
    async def reader(messages,**kwargs):
        _,sections=_parse_frame(messages[1]['content'])
        packet=json.loads(next(s.content for s in sections if s.name=='Structured State').split('\n')[-1])
        return ChatResult(content=json.dumps({'status':'answer','answer':'临时有效答复','evidence_ids':packet['allowed_evidence_ids']}))
    service.llm.chat=reader
    async def rewrite(context):context.response='unverified post-hook facts'
    service.runtime.pipeline.register(Phase.AFTER_REASONING,rewrite)
    message,_,trace=asyncio.run(service.runtime.run(sid,'我用什么语言回答',skip_memory_enqueue=True))
    assert message['content']==SAFE_REJECTED and trace['metadata']['citation_validation']['reason']=='post_hook_changed_answer'


def test_memory_answer_api_requires_login_and_same_owner(core):
    app=create_app(core[3])
    with TestClient(app) as client:
        assert client.post('/api/memory-answer',json={'query':'我的回答语言'}).status_code==401
        for user in ('alice','bob'):
            creds={'username':user,'password':'Isolated-test-password'}
            assert client.post('/api/auth/register',json=creds).status_code==201
            login=client.post('/api/auth/login',json=creds).json()
            if user=='alice':
                uid=login['user']['id'];headers={'Authorization':'Bearer '+login['access_token']}
        response=client.post('/api/memory-answer',json={'query':'我的回答语言'},headers=headers)
        assert response.status_code==200 and response.json()['reader_called'] is False
        assert client.post('/api/memory-answer',json={'query':'query','user_id':uid},headers=headers).status_code==422
        assert client.post('/api/memory-answer',json={'query':'query'},headers={**headers,'Origin':'https://external.invalid'}).status_code==403
        assert client.get('/openapi.json').json()['paths']['/api/memory-answer']['post']['security']==[{'UserSession':[]}]
