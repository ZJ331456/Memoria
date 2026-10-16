"""Temporary engineering fixtures for ingress attribution and memory visibility."""
import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from memoria.api import create_app
from memoria.api.app import _create_tenant_app
from memoria.llm import ChatResult
from memoria.llm import LLMClient
from memoria.config import Settings
from memoria.memory.processing import message_states
from memoria.memory.worker import MemoryJobWorker
from memoria.provenance import current_ingress,message_ingress
from memoria.store import Store


@pytest.fixture
def store(tmp_path):
    value=Store(tmp_path/'isolated.db','json')
    yield value
    value.close()


def state(store,mid):return message_states(store,[mid])[mid]['memory_processing']


@pytest.mark.parametrize('status',['pending','running','retry','failed','completed'])
def test_job_state_is_not_confused_with_approval(store,status):
    source=store.add_message(store.create_session()['id'],'user','工程问题：检查进度')
    job=store.enqueue_memory_job(source['id'],source['content'],'工程答复')
    with store.db:store.db.execute('UPDATE memory_jobs SET status=?,extracted_count=0,staged_count=0 WHERE id=?',(status,job['id']))
    assert state(store,source['id'])['state']==('no_candidates' if status=='completed' else status)
    assert state(store,source['id'])['approved']==0


def test_legacy_unknown_unqueued_assistant_and_revocation_are_distinct(store):
    sid=store.create_session()['id'];source=store.add_message(sid,'user','工程原话')
    assistant=store.add_message(sid,'assistant','工程回复')
    assert state(store,source['id'])['state']=='not_queued'
    assert state(store,assistant['id'])['state']=='not_applicable'
    job=store.enqueue_memory_job(source['id'],source['content'],'')
    with store.db:store.db.execute("UPDATE memory_jobs SET status='completed' WHERE id=?",(job['id'],))
    assert state(store,source['id'])['state']=='legacy_unknown'
    store.undo_memory_sources([source['id']])
    assert state(store,source['id'])['state']=='not_applicable'


@pytest.mark.parametrize('status',['pending','applying','approved','rejected'])
def test_candidate_outcome_and_assistance_are_visible(store,status):
    source=store.add_message(store.create_session()['id'],'user','工程偏好：深蓝色')
    row=store.propose_memory_review(source['id'],'工程候选','preference',3)
    with store.db:store.db.execute('UPDATE memory_reviews SET status=? WHERE id=?',(status,row['id']))
    assert state(store,source['id'])['state']=={'pending':'awaiting_review','applying':'reviewing','approved':'stored','rejected':'rejected'}[status]
    if status=='pending':
        store.record_review_assessment(row['id'],'engineering-fingerprint',{'decision':'review','reason_codes':['engineering_only']})
        assert state(store,source['id'])['state']=='needs_assistance'


def test_origin_is_per_message_not_inferred_from_renamable_title_and_is_deleted_with_source(store):
    sid=store.create_session('QQ · a title cannot assert identity')['id']
    old=store.add_message(sid,'user','工程旧消息')
    assert store.message_source(old['id'])['origin']=={'channel':'unknown','basis':'not_recorded'}
    with message_ingress('qq','a'*64):
        first=store.add_message(sid,'user','工程QQ消息')
        with message_ingress('api',preserve=True):assert current_ingress.get()['channel']=='qq'
    assert current_ingress.get() is None
    with message_ingress('chat'):second=store.add_message(sid,'user','工程网页消息')
    store.rename_session(sid,'Another channel name')
    assert store.message_source(first['id'])['origin']['channel']=='qq'
    assert store.message_source(second['id'])['origin']['channel']=='chat'
    assert 'event_ref' not in store.message_source(first['id'])['origin']
    store.delete_session(sid)
    assert store.db.execute('SELECT COUNT(*) FROM message_origins').fetchone()[0]==0


@pytest.mark.parametrize('channel',['chat','api','feishu','qq','telegram'])
@pytest.mark.parametrize('empty',[False,True])
def test_all_ingress_use_same_runtime_enqueue_and_automatic_admission(tmp_path,channel,empty):
    cfg=tmp_path/'fixture.toml'
    cfg.write_text('[storage]\ndatabase="'+(tmp_path/'runtime.db').as_posix()+'"\n[memory.markdown]\nenabled=false\n[llm.main]\nmodel="engineering"\nbase_url="https://engineering.invalid/v1"\napi_key="engineering-only"\n')
    app=_create_tenant_app(cfg);service=app.state.service;store=app.state.store
    service.memory_worker.formation=None  # Preserve this test's legacy array extractor contract.
    text='我喜欢深蓝色。';calls=[]
    async def extraction(*args):
        calls.append('extract')
        return [] if empty else [{'content':'用户喜欢深蓝色','kind':'preference','importance':3}]
    async def chat(messages,**kwargs):
        if messages[0]['content'].startswith('你是长期记忆的证据审核员'):
            calls.append('review')
            return ChatResult(content=json.dumps({'decision':'accept','assessment':'Supported','durable':True,
                'self_related':True,'conditions_preserved':True,'time_supported':True,'sensitive':False,
                'evidence_quote':text,'claim':None},ensure_ascii=False))
        return ChatResult(content='工程回答')
    service.llm.chat=chat;service.llm.extract_memories=extraction
    service.runtime.retrieval_planner.planner=None;service.runtime.memory.embedder=None
    async def run():
        sid=store.create_session('Engineering source')['id']
        with message_ingress(channel):await service.runtime.run(sid,text)
        messages=store.messages(sid);mid=messages[0]['id']
        assert all(store.message_source(m['id'])['origin']['channel']==channel for m in messages)
        assert state(store,mid)['state']=='pending'
        assert await service.memory_worker.process_once()
        assert state(store,mid)['state']==('no_candidates' if empty else 'stored')
        assert calls==(['extract'] if empty else ['extract','review'])
        assert len(store.memories())==(0 if empty else 1)
    try:asyncio.run(run())
    finally:store.close()


def test_authenticated_status_filters_and_no_client_ingress_override(tmp_path,monkeypatch):
    async def idle(_):await asyncio.Event().wait()
    monkeypatch.setattr(MemoryJobWorker,'run',idle)
    cfg=tmp_path/'fixture.toml';cfg.write_text('[storage]\ndatabase="'+(tmp_path/'data/main.db').as_posix()+'"\n')
    app=create_app(cfg)
    with TestClient(app) as client:
        body={'username':'fixture_owner','password':'Temporary-engineering-password'}
        client.post('/api/auth/register',json=body);login=client.post('/api/auth/login',json=body).json()
        sid=client.post('/api/sessions',json={'title':'Telegram fake title'}).json()['id']
        store=app.state.tenants.apps[login['user']['id']].state.store
        with message_ingress('qq'):a=store.add_message(sid,'user','工程QQ消息')
        with message_ingress('chat'):b=store.add_message(sid,'user','工程Chat消息')
        packet=client.get('/api/studio/conversations').json()['items'][0]
        assert packet['origin']['channels']==['chat','qq']
        for channel,count in [('qq',1),('chat',1),('telegram',0)]:
            assert client.get('/api/studio/conversations',params={'origin':channel}).json()['total']==count
        url='/api/studio/conversations/'+sid+'/memory-status'
        result=client.get(url,params={'ids':a['id']+','+b['id']}).json()
        assert not result['model_called'] and len(result['items'])==2
        assert result['items'][a['id']]['origin']['channel']=='qq'
        other=store.add_message(store.create_session()['id'],'user','另一会话')
        assert client.get(url,params={'ids':other['id']}).status_code==404
        assert client.post('/api/sessions/'+sid+'/chat',json={'content':'工程输入','channel':'qq'}).status_code==422
        service=app.state.tenants.apps[login['user']['id']].state.service
        async def engineering_turn(session_id,content,on_event=None,request_id=None):
            store.add_message(session_id,'user',content)
            return store.add_message(session_id,'assistant','工程HTTP答复'),[],store.add_trace(session_id,'completed',1,1,[],[])
        service.chat_with_trace=engineering_turn
        response=client.post('/api/sessions/'+sid+'/chat',json={'content':'工程HTTP输入'},headers={'X-Request-ID':'qq-forged-channel'})
        assert response.status_code==200
        assert all(store.message_source(m['id'])['origin']['channel']=='api' for m in store.messages(sid)[-2:])
        client.post('/api/auth/logout')
        assert client.get(url).status_code==401


def test_failed_message_transaction_cannot_leave_origin_or_message(store):
    sid=store.create_session()['id']
    with pytest.raises(RuntimeError),message_ingress('telegram'),store._memory_transaction():
        store.add_message(sid,'user','工程回滚原话')
        raise RuntimeError('engineering rollback')
    assert store.messages(sid)==[]
    assert store.db.execute('SELECT COUNT(*) FROM message_origins').fetchone()[0]==0


@pytest.mark.parametrize('raw',['not json','{}','[{"content":"工程","kind":"fact","importance":"3"}]'])
def test_invalid_extraction_raises_instead_of_becoming_successful_empty_result(tmp_path,raw):
    cfg=tmp_path/'fixture.toml';cfg.write_text('[memory.markdown]\nenabled=false\n')
    client=LLMClient(Settings.load(cfg))
    async def complete(*args,**kwargs):return raw
    client.complete=complete
    with pytest.raises((ValueError,json.JSONDecodeError)):
        asyncio.run(client.extract_memories('工程原话','工程答复'))
