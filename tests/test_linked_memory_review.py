"""Explicit unified approval; temporary sources, no automatic personal-state promotion."""
import asyncio
from fastapi.testclient import TestClient
from memoria.api.app import _create_tenant_app
from memoria.memory.access import MemoryAccessOrchestrator, QueryRequest


def core(tmp_path):
    config=tmp_path/'config.toml'
    config.write_text('[storage]\ndatabase="'+(tmp_path/'review.db').as_posix()+'"\n[memory.markdown]\nenabled=false\n',encoding='utf-8')
    app=_create_tenant_app(config)
    return app,TestClient(app)


def candidate(app, client, text):
    store=app.state.store
    source=store.add_message(store.create_session()['id'],'user',text)
    job=store.enqueue_memory_job(source['id'],text,'工程确认')
    store.claim_memory_job('worker')
    review=store.stage_memory_reviews(job['id'],'worker',[{'content':text,'kind':'profile'}])[0]
    store.finish_memory_job(job['id'],owner='worker')
    evidence=client.get('/api/claim-source/'+source['id']).json()['evidence']
    return review,{'content':text,'kind':'profile','importance':3,'approve_claim':True,
        'claim':{'predicate':'residence','value':'杭州','evidence':[evidence],'reason':'核对本人原话与槽位后的明确批准'}}


def test_one_explicit_review_publishes_queryable_state_and_rejects_replay(tmp_path):
    app,client=core(tmp_path)
    review,body=candidate(app,client,'我现在住在杭州')
    assert client.post('/api/memory-reviews/'+review['id']+'/approve',json={**body,'approve_claim':False}).status_code==422
    assert app.state.service.claims.list()==[]
    response=client.post('/api/memory-reviews/'+review['id']+'/approve',json=body)
    assert response.status_code==200,response.text
    assert response.json()['applied_claim_id']
    service=app.state.service
    result=asyncio.run(MemoryAccessOrchestrator(service.claims,service.runtime.memory).access(QueryRequest(query='我现在住在哪里？')))
    assert result['state']['payload']['complete']
    assert result['state']['payload']['claims'][0]['value']=='杭州'
    assert client.post('/api/memory-reviews/'+review['id']+'/approve',json=body).status_code==409
    app.state.store.close()


def test_conflicting_linked_approval_rolls_back_memory_claim_and_review(tmp_path):
    app,client=core(tmp_path)
    first,body=candidate(app,client,'我现在住在杭州')
    assert client.post('/api/memory-reviews/'+first['id']+'/approve',json=body).status_code==200
    second,body=candidate(app,client,'我现在住在上海')
    body['claim']['value']='上海'
    response=client.post('/api/memory-reviews/'+second['id']+'/approve',json=body)
    assert response.status_code==409,response.text
    assert len(app.state.store.memories())==1
    assert len(app.state.service.claims.list())==1
    assert app.state.store.memory_review(second['id'])['status']=='pending'
    app.state.store.close()


def test_unbound_value_cannot_be_approved_as_current_state(tmp_path):
    app,client=core(tmp_path)
    review,body=candidate(app,client,'我现在住在杭州')
    body['claim']['value']='北京'
    response=client.post('/api/memory-reviews/'+review['id']+'/approve',json=body)
    assert response.status_code==422,response.text
    assert app.state.store.memories()==[]
    assert app.state.service.claims.list()==[]
    assert app.state.store.memory_review(review['id'])['status']=='pending'
    app.state.store.close()
