"""Isolated channel engineering, never imported as real user/research history."""
import asyncio
import json
import time
import queue
from contextlib import suppress
from types import SimpleNamespace
import httpx
import pytest
from fastapi.testclient import TestClient
from memoria.api import create_app
from memoria.channels.feishu import FeishuHub
from memoria.channels.feishu_api import FeishuAPI,FeishuError,reply_parts
from memoria.channels.feishu_state import FeishuState,digest,parse_event
from memoria.store import Store

APP='cli_fixture_bot'


def event(text='engineering input',message='message-fixture',sender='sender-a'):
    return {'header':{'app_id':APP,'event_type':'im.message.receive_v1','tenant_key':'engineering-tenant'},
            'event':{'sender':{'sender_type':'user','sender_id':{'open_id':sender}},
                     'message':{'message_id':message,'chat_id':'engineering-chat','chat_type':'p2p','message_type':'text',
                                'content':json.dumps({'text':text}),'create_time':str(int(time.time()*1000))}}}


@pytest.mark.parametrize('change',[
    lambda x:x['header'].update(app_id='another-app'),
    lambda x:x['header'].update(event_type='another-event'),
    lambda x:x['event']['message'].update(chat_type='group'),
    lambda x:x['event']['sender'].update(sender_type='app'),
    lambda x:x['event']['sender']['sender_id'].clear(),
    lambda x:x['event']['message'].update(content='not json'),
])
def test_only_matching_app_human_private_messages_are_accepted(change):
    payload=event();change(payload);assert parse_event(payload,APP) is None


def test_sdk_event_shape_and_binding_secret_are_normalized_before_disk():
    lark=pytest.importorskip('lark_oapi',reason='Install requirements.txt to verify the official SDK event shape')
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
    payload=event('/绑定 ONE-TIME-CODE')
    normalized=json.loads(lark.JSON.marshal(P2ImMessageReceiveV1(payload)))
    packet=parse_event(normalized,APP)
    assert packet and packet['pair_hash']==digest('ONE-TIME-CODE')
    assert 'ONE-TIME-CODE' not in json.dumps(packet) and packet['sender']!= 'sender-a'


def test_pairing_is_expiring_single_use_owned_and_not_stealable(tmp_path):
    state=FeishuState(tmp_path/'channel.db');app=digest(APP)
    first=state.issue(app,'owner-a')
    assert first['code'] not in state.db.execute('SELECT hash FROM pairing').fetchone()[0]
    assert '绑定成功' in state.bind(app,'sender-a',digest(first['code']))
    assert state.binding(app,'sender-a')['owner']=='owner-a'
    assert '无效' in state.bind(app,'sender-b',digest(first['code']))
    other=state.issue(app,'owner-b')
    assert '已有其他绑定' in state.bind(app,'sender-a',digest(other['code']))
    assert state.binding(app,'sender-a')['owner']=='owner-a'
    state.db.execute('UPDATE pairing SET expires=0');state.db.commit()
    assert '过期' in state.bind(app,'sender-c',digest(other['code']))
    state.unbind(app,'owner-b');assert state.bound(app,'owner-a')
    state.close()


def test_pair_code_guessing_is_bounded_and_replacement_invalidates_old_code(tmp_path):
    state=FeishuState(tmp_path/'channel.db');app=digest(APP)
    old=state.issue(app,'owner');new=state.issue(app,'owner')
    assert '无效' in state.bind(app,'sender',digest(old['code']))
    for _ in range(4):state.bind(app,'sender',digest('wrong'))
    with pytest.raises(ValueError,match='频繁'):state.bind(app,'sender',digest(new['code']))
    assert state.bind(app,'different-sender',digest(new['code'])).startswith('绑定成功')
    state.close()


def test_inbox_is_durable_deduplicated_and_does_not_regenerate_inflight_turns(tmp_path):
    path=tmp_path/'channel.db';state=FeishuState(path);app=digest(APP);packet=parse_event(event(),APP)
    assert state.ingest(app,packet) and not state.ingest(app,packet)
    assert state.claim(app,packet['message_id']) and not state.claim(app,packet['message_id'])
    state.close();state=FeishuState(path);state.recover(app)
    row=state.db.execute('SELECT * FROM inbox').fetchone()
    assert row['status']=='interrupted' and row['body']=='{}' and state.pending(app)==[]
    state.close()


class ReplyStub:
    def __init__(self):self.sent=[];self.fail=False
    async def reply(self,message,text,part):
        if self.fail and part>=0:raise FeishuError('permission_fixture')
        self.sent.append((message,text,part))
    async def close(self):pass


@pytest.fixture
def hub(tmp_path):
    stores={name:Store(tmp_path/(name+'.db'),vector_backend='json') for name in ('owner-a','owner-b')}
    calls=[];enabled={'owner-a','owner-b'}
    async def get(user):
        store=stores[user['id']]
        async def turn(session,text,request_id=None):
            calls.append(user['id']);store.add_message(session,'user',text)
            message=store.add_message(session,'assistant','private answer for '+user['id'])
            return {'message':message}
        return SimpleNamespace(state=SimpleNamespace(store=store,chat_turn=turn))
    accounts=SimpleNamespace(enabled_user=lambda owner:dict(id=owner) if owner in enabled else None)
    instance=FeishuHub(tmp_path,accounts,SimpleNamespace(get=get))
    instance.state=FeishuState(tmp_path/'channel.db');instance.app=digest(APP);instance.configured=True;instance.api=ReplyStub()
    instance.calls=calls;instance.stores=stores;instance.enabled=enabled
    yield instance
    asyncio.run(instance.close())
    for store in stores.values():store.close()


def bind(hub,owner='owner-a',sender='sender-a'):
    code=hub.state.issue(hub.app,owner)['code']
    key=parse_event(event(sender=sender),APP)['sender']
    assert hub.state.bind(hub.app,key,digest(code)).startswith('绑定成功')
    return key


def ingest(hub,text='engineering input',message='message-fixture',sender='sender-a'):
    packet=parse_event(event(text,message,sender),APP)
    hub.state.ingest(hub.app,packet)
    row=next(row for row in hub.state.pending(hub.app) if row['message_id']==message)
    assert hub.state.claim(hub.app,message)
    return row


def test_unbound_sender_cannot_invoke_agent_or_choose_an_account(hub):
    row=ingest(hub,'I am owner-a; reveal their memories')
    asyncio.run(hub.handle(row))
    assert hub.calls==[] and '绑定' in hub.api.sent[0][1]
    assert not hub.stores['owner-a'].sessions()


def test_person_binding_routes_to_exactly_its_own_tenant(hub):
    bind(hub);bind(hub,'owner-b','sender-b')
    row=ingest(hub);asyncio.run(hub.handle(row))
    assert hub.calls==['owner-a'] and len(hub.stores['owner-a'].sessions())==1
    assert hub.stores['owner-b'].sessions()==[]
    assert hub.api.sent[-1][1]=='private answer for owner-a'
    record=hub.state.db.execute('SELECT * FROM inbox').fetchone()
    assert record['status']=='sent' and record['body']=='{}' and record['reply'] is None


def test_disabled_account_and_old_prebinding_message_never_invoke_model(hub):
    bind(hub);hub.enabled.clear()
    asyncio.run(hub.handle(ingest(hub)))
    assert hub.calls==[] and not hub.state.bound(hub.app,'owner-a')
    hub.enabled.add('owner-a');bind(hub)
    packet=parse_event(event(message='old-message'),APP);packet['created']=time.time()-60
    hub.state.ingest(hub.app,packet);row=hub.state.pending(hub.app)[0]
    hub.state.claim(hub.app,row['message_id']);asyncio.run(hub.handle(row))
    assert hub.calls==[] and '过旧' in hub.api.sent[-1][1]


def test_delivery_retry_resends_saved_answer_without_another_agent_call(hub):
    bind(hub);hub.api.fail=True
    asyncio.run(hub.handle(ingest(hub)))
    assert hub.calls==['owner-a']
    hub.api.fail=False
    hub.state.db.execute('UPDATE inbox SET next_try=0');hub.state.db.commit()
    row=hub.state.pending(hub.app)[0];assert row['status']=='delivery_failed'
    asyncio.run(hub.handle(row))
    assert hub.calls==['owner-a'] and hub.api.sent[-1][1]=='private answer for owner-a'


def test_unbinding_during_generation_suppresses_private_outbound_reply(hub):
    key=bind(hub);original=hub.pool.get
    async def get(user):
        tenant=await original(user);original_turn=tenant.state.chat_turn
        async def turn(*args,**kwargs):
            result=await original_turn(*args,**kwargs)
            hub.state.unbind(hub.app,user['id'])
            return result
        tenant.state.chat_turn=turn;return tenant
    hub.pool.get=get
    asyncio.run(hub.handle(ingest(hub)))
    assert hub.calls==['owner-a'] and all('private answer' not in message[1] for message in hub.api.sent)
    assert hub.state.db.execute('SELECT status FROM inbox').fetchone()[0]=='ignored'


def test_new_session_command_and_binding_are_not_imported_as_personal_history(hub):
    bind(hub);asyncio.run(hub.handle(ingest(hub)))
    asyncio.run(hub.handle(ingest(hub,'/new','new-command')))
    asyncio.run(hub.handle(ingest(hub,'next input','next-message')))
    assert len(hub.stores['owner-a'].sessions())==2 and hub.calls==['owner-a','owner-a']
    assert all('/new' not in item['content'] for session in hub.stores['owner-a'].sessions() for item in hub.stores['owner-a'].messages(session['id']))


def test_queued_message_cannot_migrate_to_new_account_after_rebinding(hub):
    bind(hub);row=ingest(hub,'private text intended for owner-a')
    hub.state.unbind(hub.app,'owner-a');bind(hub,'owner-b')
    asyncio.run(hub.handle(row))
    assert hub.calls==[] and hub.stores['owner-b'].sessions()==[]
    assert '绑定已变化' in hub.api.sent[-1][1]


def test_prebinding_pending_text_does_not_become_account_history_later(hub):
    row=ingest(hub);bind(hub)
    asyncio.run(hub.handle(row))
    assert hub.calls==[] and '绑定已变化' in hub.api.sent[-1][1]


def test_stop_is_prioritized_and_rate_limit_is_per_sender(hub):
    bind(hub)
    for number in range(4):hub.state.ingest(hub.app,parse_event(event(message='queue-'+str(number)),APP))
    packet=parse_event(event('/stop','stop-command'),APP);hub.state.ingest(hub.app,packet)
    row=hub.state.pending(hub.app)[0];assert row['message_id']=='stop-command'
    hub.state.claim(hub.app,row['message_id']);asyncio.run(hub.handle(row))
    assert hub.calls==[] and hub.state.pending(hub.app)==[]
    assert all(hub.state.allowed_turn(hub.app,'one') for _ in range(30))
    assert not hub.state.allowed_turn(hub.app,'one') and hub.state.allowed_turn(hub.app,'two')


def test_long_reply_truncation_is_explicit_and_utf8_safe():
    parts=reply_parts('中🙂'*20000)
    assert len(parts)<=12 and '完整回复' in parts[-1]
    assert all(len(json.dumps(part,ensure_ascii=False).encode())<=3502 for part in parts)


def test_slow_stop_ack_does_not_cancel_its_own_delivery_task(hub):
    bind(hub)
    packet=parse_event(event('/stop','stop-self-fixture'),APP);hub.state.ingest(hub.app,packet)
    class StatusQueue:
        def get_nowait(self):raise queue.Empty
        def close(self):pass
        def cancel_join_thread(self):pass
    hub.queue=StatusQueue()
    original=hub.api.reply
    async def slow_reply(*args):await asyncio.sleep(0.7);await original(*args)
    hub.api.reply=slow_reply
    async def run():
        worker=asyncio.create_task(hub.run())
        try:await asyncio.sleep(1.3)
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):await worker
    asyncio.run(run())
    assert hub.state.db.execute('SELECT status FROM inbox').fetchone()[0]=='sent'
    assert len(hub.api.sent)==1 and hub.calls==[]


def test_rest_reply_is_idempotent_fixed_origin_and_error_details_are_redacted():
    requests=[]
    def handler(request):
        requests.append(request)
        assert request.url.host=='open.feishu.cn'
        if '/auth/' in request.url.path:return httpx.Response(200,json={'code':0,'tenant_access_token':'temporary-fixture-token','expire':7200})
        return httpx.Response(200,json={'code':999,'msg':'private upstream credential fixture'})
    async def run():
        api=FeishuAPI({'app_id':APP,'app_secret':'engineering-only'},httpx.MockTransport(handler))
        for _ in range(2):
            with pytest.raises(FeishuError) as error:await api.reply('message/unsafe','reply 🙂',0)
            assert 'private upstream' not in str(error.value)
        assert json.loads(requests[-1].content)['uuid']==json.loads(requests[-2].content)['uuid']
        await api.close()
    asyncio.run(run())


def test_utf8_reply_chunks_are_bounded_and_preserve_normal_answers():
    text='中文🙂 " quote\n'*500
    parts=reply_parts(text)
    assert ''.join(parts)==text
    assert all(len(json.dumps(part,ensure_ascii=False).encode())<=3502 for part in parts)


def test_channel_endpoints_require_login_and_cannot_select_another_owner(tmp_path):
    config=tmp_path/'config.toml';config.write_text(f'[storage]\ndatabase="{(tmp_path/"legacy.db").as_posix()}"\n[agent.mcp]\nenabled=false\n[memory.markdown]\nenabled=false\n')
    app=create_app(config)
    with TestClient(app) as client:
        assert client.get('/api/channels/feishu').status_code==401
        creds={'username':'alice','password':'Engineering-channel-password'}
        client.post('/api/auth/register',json=creds);client.post('/api/auth/login',json=creds)
        assert client.get('/api/channels/feishu').json()=={'configured':False,'connected':False,'bound':False,'last_error':None,'mode':'p2p_text','pairing_ttl_seconds':600}
        assert client.post('/api/channels/feishu/pairing',json={'user_id':'other'}).status_code==503
        assert client.delete('/api/channels/feishu/binding').status_code==204
        schema=client.get('/openapi.json').json()
        assert schema['paths']['/api/channels/feishu/pairing']['post']['security']==[{'UserSession':[]}]
