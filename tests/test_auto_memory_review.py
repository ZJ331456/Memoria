"""Model admission protocol tests use temporary sources and explicit model fixtures."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from memoria.api.app import _create_tenant_app
from memoria.claims import ClaimError
from memoria.llm.types import ChatResult


@pytest.fixture
def env(tmp_path):
    config=tmp_path/'config.toml'
    config.write_text('[storage]\ndatabase="'+(tmp_path/'memory.db').as_posix()+'"\n[memory.markdown]\nenabled=false\n[llm.main]\nmodel="engineering-model"\nbase_url="https://model.invalid/v1"\napi_key="engineering-only"\n')
    app=_create_tenant_app(config)
    yield app,TestClient(app),app.state.store,app.state.service
    app.state.store.close()


def candidate(store,text='我平时喜欢用中文回答。',content='用户偏好中文回答'):
    source=store.add_message(store.create_session()['id'],'user',text)
    return source,store.propose_memory_review(source['id'],content,'preference',3)


def model(service,text,*,changes=None,claim=True,race=None):
    data={'decision':'accept','assessment':'Supported','durable':True,'self_related':True,'conditions_preserved':True,
          'time_supported':True,'sensitive':False,'evidence_quote':text,'claim':{'predicate':'response_language','value':'中文','polarity':'positive'} if claim else None}
    data.update(changes or {})
    calls=[]
    async def chat(messages,**kwargs):
        calls.append(messages)
        if race:race()
        return ChatResult(content=json.dumps(data,ensure_ascii=False),raw_message={},usage={'total_tokens':42})
    service.llm.chat=chat
    return calls


def test_low_risk_automatic_atomic_memory_claim_and_audit(env):
    _,_,store,service=env
    source,row=candidate(store)
    calls=model(service,source['content'])
    result=asyncio.run(service.auto_review.review(row['id']))
    assert result['status']=='approved' and result['applied_memory_id'] and result['applied_claim_id']
    assert len(store.memories())==1 and service.claims.list()[0]['status']=='approved'
    assert result['assessment']['reviewer']=='llm' and result['assessment']['usage']['total_tokens']==42
    assert asyncio.run(service.auto_review.review(row['id']))['status']=='approved' and len(calls)==1
    assert len(store.memory_reviews('all')[0]['assessment']['input_sha256'])==64


@pytest.mark.parametrize('text,changes,code',[
    ('我确诊有哮喘。',{},'sensitive_personal_fact'),
    ('我的朋友喜欢中文回答。',{},'third_party_or_mixed_subject'),
    ('面试时我喜欢用中文回答。',{},'complex_condition'),
    ('我计划搬去杭州。',{},'plan_or_hypothesis'),
    ('我不喜欢中文回答。',{},'unbound_negation'),
    ('我喜欢用中文回答。',{'assessment':'Insufficient'},'insufficient_support'),
    ('我喜欢用中文回答。',{'evidence_quote':'原话不存在'},'quote_not_unique_or_missing'),
    ('去年我住在杭州。',{},'temporal_or_transition_requires_review'),
    ('我周末喜欢跑步。',{},'complex_condition'),
])
def test_model_accept_cannot_override_independent_risk_gates(env,text,changes,code):
    _,_,store,service=env
    source,row=candidate(store,text)
    model(service,text,changes=changes,claim=False)
    result=asyncio.run(service.auto_review.review(row['id']))
    assert result['status']=='pending' and code in result['assessment']['reason_codes']
    assert store.memories()==[] and service.claims.list()==[]


def test_non_durable_reject_and_credentials_never_reach_model(env):
    _,_,store,service=env
    source,row=candidate(store,'今天怎么做菜？','用户问了做菜问题')
    calls=model(service,source['content'],changes={'durable':False},claim=False)
    assert asyncio.run(service.auto_review.review(row['id']))['status']=='rejected'
    _,secret=candidate(store,'api_key: ENGINEERING-CREDENTIAL-ONLY','临时凭据')
    result=asyncio.run(service.auto_review.review(secret['id']))
    assert result['status']=='rejected' and not result['assessment']['model_called'] and len(calls)==1
    assert store.memories()==[]


def test_failed_assessment_is_not_repeated_by_background_loop(env):
    _,_,store,service=env
    source,row=candidate(store)
    calls=model(service,source['content'],changes={'unexpected_field':'invalid'})
    asyncio.run(service.auto_review.process_next())
    result=store.memory_review(row['id'])
    assert result['status']=='pending' and result['assessment']['reason_codes']==['model_or_publish_failed']
    assert result['assessment']['usage']['total_tokens']==42
    asyncio.run(service.auto_review.process_next())
    assert len(calls)==1


def test_source_revoked_during_model_does_not_publish(env):
    _,_,store,service=env
    source,row=candidate(store)
    model(service,source['content'],race=lambda:store.undo_memory_sources([source['id']]))
    result=asyncio.run(service.auto_review.review(row['id']))
    assert result['status']=='pending' and store.memories()==[] and service.claims.list()==[]


def test_conflicting_current_state_rolls_back_everything(env):
    _,_,store,service=env
    source,row=candidate(store)
    model(service,source['content']);asyncio.run(service.auto_review.review(row['id']))
    source,new=candidate(store,'我现在喜欢用英文回答。','用户偏好英文回答')
    model(service,source['content'],changes={'claim':{'predicate':'response_language','value':'英文','polarity':'positive'}})
    result=asyncio.run(service.auto_review.review(new['id']))
    assert result['status']=='pending' and result['assessment']['reason_codes']==['existing_state_requires_revision']
    assert len(store.memories())==1 and len(service.claims.list())==1
    proposal=result['assessment']['structured_proposal']
    assert not proposal['automatic_approval'] and proposal['value']=='英文'
    from memoria.memory.access import QueryRequest
    request=QueryRequest(query='我的回答语言是什么？')
    access=asyncio.run(service.kernel.access(request))
    assert access['path']=='clarify' and access['pending_revisions'][0]['review_id']==new['id']
    answer=asyncio.run(service.kernel.answer(request))
    assert answer['reader_called'] is False and answer['status']=='clarify'
    assert service.claims.get(proposal['target_claim_id'])['revision_id']==proposal['expected_revision_id']
    from memoria.claims import AccessRequest
    raw_request=AccessRequest(predicates=['response_language'])
    raw=service.claims.access(raw_request)
    dependency=service.claims.access_dependency(raw_request,raw['payload'])
    assert not service.kernel.dependency_valid(dependency)
    store.reject_memory_review(new['id'])
    assert not asyncio.run(service.kernel.access(request)).get('pending_revisions')
    assert service.kernel.dependency_valid(dependency)


def test_conflict_proposal_arriving_during_reader_blocks_old_answer(env):
    _,_,store,service=env
    source,first=candidate(store);model(service,source['content'])
    asyncio.run(service.auto_review.review(first['id']))
    source,new=candidate(store,'我平时喜欢用英文回答。','用户偏好英文回答')
    model(service,source['content'],changes={'claim':{'predicate':'response_language','value':'英文','polarity':'positive'}})
    reviewer=service.llm.chat
    from memoria.prompting.assembler import _parse_frame
    from memoria.memory.access import QueryRequest
    async def reader(messages,**kwargs):
        _,sections=_parse_frame(messages[1]['content'])
        packet=json.loads(next(s.content for s in sections if s.name=='Structured State').rsplit('\n',1)[-1])
        service.llm.chat=reviewer
        await service.auto_review.review(new['id'])
        return ChatResult(content=json.dumps({'status':'answer','answer':'中文','evidence_ids':packet['allowed_evidence_ids']}))
    service.llm.chat=reader
    result=asyncio.run(service.kernel.answer(QueryRequest(query='我的回答语言是什么？')))
    assert result['status']=='abstain' and result['reader_called']
    assert result['validation']['reason']=='unresolved_revision_proposal'
    assert result['closed_loop']['pending_revisions'][0]['source_ref']==source['id']
    store.undo_memory_sources([source['id']])
    assert not asyncio.run(service.kernel.access(QueryRequest(query='我的回答语言是什么？'))).get('pending_revisions')


def test_approving_only_ordinary_memory_does_not_resolve_a_structured_revision(env):
    _,client,store,service=env
    source,first=candidate(store);model(service,source['content'])
    asyncio.run(service.auto_review.review(first['id']))
    source,new=candidate(store,'我平时喜欢用英文回答。','用户偏好英文回答')
    model(service,source['content'],changes={'claim':{'predicate':'response_language','value':'英文','polarity':'positive'}})
    asyncio.run(service.auto_review.review(new['id']))
    service.runtime.memory.decider=None
    response=client.post('/api/memory-reviews/'+new['id']+'/approve',json={
        'content':new['content'],'kind':new['kind'],'importance':new['importance']})
    assert response.status_code==200 and response.json()['status']=='approved'
    from memoria.memory.access import QueryRequest
    access=asyncio.run(service.kernel.access(QueryRequest(query='我的回答语言是什么？')))
    assert access['path']=='clarify' and access['pending_revisions']


def test_same_state_does_not_require_human_or_create_duplicate_claim(env):
    _,_,store,service=env
    source,row=candidate(store)
    model(service,source['content']);first=asyncio.run(service.auto_review.review(row['id']))
    source,new=candidate(store)
    model(service,source['content']);result=asyncio.run(service.auto_review.review(new['id']))
    assert result['status']=='approved' and result['applied_claim_id']==first['applied_claim_id']
    assert len(store.memories())==1 and len(service.claims.list())==1


def test_historical_extraction_retries_empty_once_preserving_receipt(env):
    _,client,store,_=env
    source,_=candidate(store)
    job=store.enqueue_memory_job(source['id'],source['content'],'temporary')
    store.claim_memory_job('worker');store.stage_memory_reviews(job['id'],'worker',[]);store.finish_memory_job(job['id'],owner='worker')
    response=client.post('/api/messages/'+source['id']+'/memory-extraction',json={})
    assert response.status_code==200 and response.json()['status']=='pending'
    assert client.post('/api/messages/'+source['id']+'/memory-extraction',json={}).json()['id']==job['id']
    assert store.db.execute('SELECT COUNT(*) FROM memory_job_reprocessing').fetchone()[0]==1


def test_manual_alias_gets_semantic_review_instead_of_literal_veto(env):
    _,client,store,service=env
    source,row=candidate(store,'我喜欢慢速跑步。','用户喜欢慢跑')
    calls=[]
    async def supported(messages,**kwargs):
        calls.append(messages);return ChatResult(content='{"assessment":"Supported"}',raw_message={},usage={'total_tokens':20})
    service.llm.chat=supported
    evidence=client.get('/api/claim-source/'+source['id']).json()['evidence']
    body={'content':row['content'],'kind':'preference','importance':3,'approve_claim':True,
          'claim':{'predicate':'hobby','value':'慢跑','evidence':[evidence]}}
    response=client.post('/api/memory-reviews/'+row['id']+'/approve',json=body)
    assert response.status_code==200,response.text
    assert len(calls)==1 and response.json()['assessment']['assessment']=='Supported'


def test_edited_auto_review_payload_is_validated_and_reassessed(env):
    _,client,store,service=env
    source,row=candidate(store)
    model(service,source['content'])
    result=client.post('/api/memory-reviews/'+row['id']+'/auto-review',json={'content':'用户使用中文回答','kind':'preference','importance':3})
    assert result.status_code==200,result.text
    assert result.json()['status']=='approved' and store.memories()[0]['content']=='用户使用中文回答'


def test_assessment_and_history_routes_reject_public_sources(env):
    _,client,store,service=env
    source,row=candidate(store)
    with store.db:
        store.db.execute('INSERT INTO dataset_imports VALUES(?,?,?,?,?)',('public','sha','{}',
            json.dumps({'source_mapping':[{'session_id':source['session_id'],'message_ids':[source['id']]}]}),source['created_at']))
    calls=model(service,source['content'])
    assert client.post('/api/messages/'+source['id']+'/memory-extraction',json={}).status_code in (409,422)
    assert client.post('/api/memory-reviews/'+row['id']+'/auto-review',json={}).status_code in (409,422)
    assert calls==[] and store.memories()==[]


def test_worker_automatically_extracts_reviews_and_publishes(env):
    _,_,store,service=env
    service.memory_worker.formation=None  # Explicit legacy extraction protocol; formation is covered separately.
    text='我平时喜欢用中文回答。'
    source=store.add_message(store.create_session()['id'],'user',text)
    async def extraction(*args):return [{'content':'用户偏好中文回答','kind':'preference','importance':3}]
    service.llm.extract_memories=extraction
    model(service,text)
    job=store.enqueue_memory_job(source['id'],text,'临时确认')
    assert asyncio.run(service.memory_worker.process_once())
    assert store.memory_job(job['id'])['status']=='completed'
    assert store.memory_reviews('all')[0]['status']=='approved' and len(store.memories())==1


def test_manual_alias_model_cannot_bypass_revocation(env):
    _,client,store,service=env
    source,row=candidate(store,'我喜欢慢速跑步。','用户喜欢慢跑')
    async def changed(*args,**kwargs):
        store.undo_memory_sources([source['id']])
        return ChatResult(content='{"assessment":"Supported"}',raw_message={})
    service.llm.chat=changed
    evidence=client.get('/api/claim-source/'+source['id']).json()['evidence']
    body={'content':row['content'],'kind':'preference','importance':3,'approve_claim':True,
          'claim':{'predicate':'hobby','value':'慢跑','evidence':[evidence]}}
    assert client.post('/api/memory-reviews/'+row['id']+'/approve',json=body).status_code==409
    assert not store.memories() and not service.claims.list()


def test_auto_lease_prevents_concurrent_manual_duplicate(env):
    _,_,store,service=env
    source,row=candidate(store)
    started=asyncio.Event();release=asyncio.Event()
    model(service,source['content']);reply=service.llm.chat
    async def waiting(*args,**kwargs):
        started.set();await release.wait();return await reply(*args,**kwargs)
    service.llm.chat=waiting
    async def run():
        task=asyncio.create_task(service.auto_review.review(row['id']))
        await started.wait()
        assert store.claim_memory_review(row['id'],row['content'],row['kind'],3) is None
        release.set();return await task
    assert asyncio.run(run())['status']=='approved' and len(store.memories())==1


def test_assisted_backlog_does_not_starve_new_automatic_candidate(env):
    _,_,store,service=env
    for index in range(101):
        _,old=candidate(store,content='临时工程辅助记录 '+str(index))
        store.record_review_assessment(old['id'],'old-input',{'decision':'review','input_sha256':'old-input','reviewer':'policy'})
    source,row=candidate(store)
    calls=model(service,source['content'])
    asyncio.run(service.auto_review.process_next())
    assert store.memory_review(row['id'])['status']=='approved' and len(calls)==1
