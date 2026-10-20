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


def test_three_default_and_inherited_conditional_claims_publish_independently(env):
    from memoria.claims import AccessRequest
    _,_,store,service=env
    text='我平时用中文交流，面试模拟时用英文，而且回答尽量简洁。'
    source=store.add_message(store.create_session()['id'],'user',text)
    cases=[('中文','response_language',{},'我平时用中文交流'),
           ('英文','response_language',{'activity':'interview'},'面试模拟时用英文'),
           ('简洁','response_detail',{'activity':'interview'},'而且回答尽量简洁')]
    for value,predicate,scope,quote in cases:
        row=store.propose_memory_review(source['id'],quote,'preference',3)
        model(service,quote,changes={'claim':{'predicate':predicate,'value':value,'scope':scope}})
        result=asyncio.run(service.auto_review.review(row['id']))
        assert result['status']=='approved',result['assessment']
        claim=service.claims.get(result['applied_claim_id'])
        assert claim['scope']==scope and claim['value']==value
        assert bool(claim['condition_evidence'])==bool(scope)
    default=service.claims.access(AccessRequest(predicates=['response_language'],scope_mode='default'))
    interview=service.claims.access(AccessRequest(predicates=['response_language','response_detail'],context={'activity':'interview'}))
    assert [c['value'] for c in default['payload']['claims']]==['中文']
    assert {c['value'] for c in interview['payload']['claims']}=={'英文','简洁'}


@pytest.mark.parametrize('text,scope',[
    ('如果客户同意，面试模拟时用英文。',{'activity':'interview'}),
    ('只有拿到offer，面试模拟时用英文。',{'activity':'interview'}),
    ('面试模拟时用英文。',{}),
    ('我平时用英文。',{'activity':'interview'}),
    ('这次面试模拟时用英文。',{'activity':'interview','temporary':True}),
    ('我周末用英文。',{}),
    ('面试模拟时周末用英文。',{'activity':'interview'}),
    ('面试模拟时用英文，如果客户同意。',{'activity':'interview'}),
    ('假如面试模拟时用英文。',{'activity':'interview'}),
    ('非面试模拟时用英文。',{'activity':'interview'}),
])
def test_conditional_model_accept_cannot_erase_or_invent_conditions(env,text,scope):
    _,_,store,service=env
    source,row=candidate(store,text)
    model(service,text,changes={'claim':{'predicate':'response_language','value':'英文','scope':scope}})
    result=asyncio.run(service.auto_review.review(row['id']))
    assert result['status']=='pending' and not store.memories() and not service.claims.list()


def test_new_approved_self_report_reinforces_claim_and_survives_first_parent_revocation(env):
    from memoria.claims import AccessRequest,ViewRequest
    _,_,store,service=env
    source,one=candidate(store,'我用中文交流。','用户用中文交流')
    model(service,source['content']);one=asyncio.run(service.auto_review.review(one['id']))
    first=service.claims.get(one['applied_claim_id'])
    source2,two=candidate(store,'我的回复语言是汉语。','用户回复语言为汉语')
    model(service,source2['content']);two=asyncio.run(service.auto_review.review(two['id']))
    assert two['status']=='approved' and two['applied_claim_id']==first['id']
    current=service.claims.get(first['id'])
    assert len(current['support_groups'])==2 and len(service.claims.list())==1
    assert two['assessment']['structured_action']=='reinforce'
    assert two['assessment']['applied_claim_revision_id']==current['revision_id']
    assert {g['memory_id'] for g in current['support_groups']}=={one['applied_memory_id'],two['applied_memory_id']}
    view=service.claims.compile(ViewRequest(predicates=['response_language']))
    store.undo_memory_sources([source['id']])
    assert not service.claims.view(view['id'])['valid']
    state=service.claims.access(AccessRequest(predicates=['response_language']))
    assert state['payload']['complete']
    assert state['payload']['claims'][0]['support_group_ids']==[source2['id']]
    store.undo_memory_sources([source2['id']])
    assert service.claims.access(AccessRequest(predicates=['response_language']))['path']=='clarify'


def test_temporary_conditional_admission_binds_only_its_source_session(env):
    from memoria.claims import AccessRequest
    _,_,store,service=env
    text='本次会话面试模拟时用英文。'
    source,row=candidate(store,text,'本次会话面试模拟时使用英文')
    model(service,text,changes={'claim':{'predicate':'response_language','value':'英文',
        'scope':{'activity':'interview','temporary':True}}})
    result=asyncio.run(service.auto_review.review(row['id']))
    assert result['status']=='approved',result['assessment']
    claim=service.claims.get(result['applied_claim_id'])
    assert claim['lifecycle']['session_id']==source['session_id']
    same=AccessRequest(predicates=['response_language'],context={'activity':'interview'},session_id=source['session_id'])
    other=AccessRequest(predicates=['response_language'],context={'activity':'interview'},session_id=store.create_session()['id'])
    assert service.claims.access(same)['payload']['complete']
    assert service.claims.access(other)['path']=='clarify'


def test_claim_for_another_observation_cannot_be_published(env):
    _,_,store,service=env
    text='我平时用中文交流，面试模拟时用英文。'
    source,row=candidate(store,text,'用户平时用中文')
    start=0;end=text.index('，')
    from memoria.memory import formation_store
    formation_store.install(store)
    with store._memory_transaction():
        job=store.enqueue_memory_job(source['id'],text,'')
        receipt={'source_sha':__import__('memoria.claims',fromlist=['sha']).sha(text),
            'model':'engineering','usage':None,'observations':[{'category':'preference','topic':'语言','scope':'',
            'polarity':'positive','content':row['content'],'quote':text[start:end],'asserted':True,'durable':True,'importance':3}]}
        formation_store.stage(store,job,receipt,[row])
    model(service,'面试模拟时用英文',changes={'claim':{'predicate':'response_language','value':'英文','scope':{'activity':'interview'}}})
    result=asyncio.run(service.auto_review.review(row['id']))
    assert result['status']=='pending' and not service.claims.list()


@pytest.mark.parametrize('assessment',['Supported','Insufficient'])
def test_model_review_of_new_state_holds_only_verified_conflict(env,assessment):
    from memoria.memory.access import QueryRequest
    _,_,store,service=env
    source,first=candidate(store);model(service,source['content'])
    asyncio.run(service.auto_review.review(first['id']))
    source,row=candidate(store,'我通常用英文交流。','用户通常用英文交流')
    model(service,source['content'],changes={'decision':'review','assessment':assessment,
        'claim':{'predicate':'response_language','value':'English','polarity':'positive'}})
    reviewed=asyncio.run(service.auto_review.review(row['id']))
    access=asyncio.run(service.kernel.access(QueryRequest(query='我的回答语言是什么？')))
    assert reviewed['status']=='pending' and len(store.memories())==1
    if assessment=='Supported':
        assert reviewed['assessment']['structured_proposal']['automatic_approval'] is False
        assert access['path']=='clarify' and access['pending_revisions'][0]['source_ref']==source['id']
    else:
        assert 'structured_proposal' not in reviewed['assessment'] and not access.get('pending_revisions')


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
