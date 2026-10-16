"""Isolated protocol/ownership fixtures; no real people, API calls or research scores."""
import asyncio
import json
import logging
import time
from datetime import datetime,timezone
from types import SimpleNamespace
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from memoria.api import create_app
from memoria.channels.config import validate_bot_configuration
from memoria.channels.errors import ChannelError
from memoria.channels.qq import QQAPI,QQHub,gateway_url,parse_event
from memoria.channels.telegram import TelegramAPI,TelegramHub,parse_update
from memoria.channels.state import ChannelState,digest
from memoria.store import Store

TOKEN='123456:engineering_fixture_token_value'


def telegram(text='engineering source'):
    return {'update_id':7,'message':{'message_id':3,'date':int(time.time()),'text':text,
            'chat':{'id':123,'type':'private'},'from':{'id':123,'is_bot':False}}}


def qq(text='engineering source'):
    return {'op':0,'t':'C2C_MESSAGE_CREATE','s':9,'d':{'id':'engineering-message','content':text,
        'timestamp':datetime.now(timezone.utc).isoformat(),'author':{'user_openid':'engineering-openid'}}}


@pytest.mark.parametrize('platform',['telegram','qq'])
def test_pairing_code_is_scrubbed_before_disk_and_senders_are_namespaced(platform,tmp_path):
    source=parse_update(telegram('/bind PRIVATE-ENGINEERING-CODE')) if platform=='telegram' else parse_event(qq('/bind PRIVATE-ENGINEERING-CODE'))
    assert source['text']=='/bind' and source['pair_hash']==digest('PRIVATE-ENGINEERING-CODE')
    state=ChannelState(tmp_path/'channel.db')
    state.ingest('app',source)
    assert 'PRIVATE-ENGINEERING-CODE' not in state.db.execute('SELECT body FROM inbox').fetchone()[0]
    state.close()


@pytest.mark.parametrize('change',[
    lambda x:x['message']['chat'].update(type='group'),
    lambda x:x['message']['from'].update(is_bot=True),
    lambda x:x['message']['from'].update(id=456),
    lambda x:x.update(edited_message=x.pop('message')),
])
def test_telegram_only_private_human_original_messages(change):
    source=telegram();change(source);assert parse_update(source) is None


@pytest.mark.parametrize('change',[
    lambda x:x.update(t='GROUP_AT_MESSAGE_CREATE'),
    lambda x:x['d']['author'].update(bot=True),
    lambda x:x['d'].update(timestamp='unknown'),
    lambda x:x['d']['author'].pop('user_openid'),
])
def test_qq_only_official_c2c_with_actual_timestamp(change):
    source=qq();change(source);assert parse_event(source) is None


@pytest.mark.parametrize('url',['ws://api.bot.qq.com/websocket','wss://other.test/websocket','wss://user:password@api.bot.qq.com/websocket','wss://api.bot.qq.com:8000/websocket'])
def test_qq_gateway_cannot_redirect_authentication_to_another_origin(url):
    with pytest.raises(ChannelError):gateway_url(url)


def test_qq_rest_authentication_cache_passive_id_and_stable_sequence():
    async def run():
        requests=[]
        def respond(request):
            requests.append(request)
            assert request.url.host=='api.bot.qq.com'
            if request.url.path=='/app/getAppAccessToken':
                assert json.loads(request.content)=={'appId':'12345678','clientSecret':'engineering-secret'}
                return httpx.Response(200,json={'access_token':'engineering-access-token','expires_in':'7200'})
            assert request.headers['authorization']=='QQBot engineering-access-token'
            if request.url.path=='/gateway':return httpx.Response(200,json={'url':'wss://api.bot.qq.com/websocket'})
            body=json.loads(request.content)
            assert body['msg_id']=='engineering-message' and body['msg_seq']==2
            return httpx.Response(200,json={'id':'engineering-reply'})
        api=QQAPI({'app_id':'12345678','app_secret':'engineering-secret'},transport=httpx.MockTransport(respond))
        assert await api.gateway()=='wss://api.bot.qq.com/websocket'
        await api.reply(parse_event(qq()),'reply',0)
        assert sum(r.url.path=='/app/getAppAccessToken' for r in requests)==1
        await api.close()
    asyncio.run(run())


def test_qq_reconnect_resumes_only_after_message_is_durably_ingested(tmp_path,monkeypatch):
    async def run():
        hub=QQHub(tmp_path,None,None,configuration={},project=tmp_path)
        hub.app='engineering';hub.state=ChannelState(tmp_path/'channel.db')
        async def gateway():return 'wss://api.bot.qq.com/websocket'
        async def authenticate():return 'engineering-access-token'
        hub.api=SimpleNamespace(gateway=gateway,authenticate=authenticate)
        auth=[];rounds=[]
        class Socket:
            def __init__(self,index):
                dispatch=qq();dispatch['s']=5
                self.events=[{'op':10,'d':{'heartbeat_interval':60000}},
                    {'op':0,'t':'READY','s':4,'d':{'session_id':'engineering-session'}},dispatch] if index==0 else [
                    {'op':10,'d':{'heartbeat_interval':60000}},{'op':0,'t':'RESUMED','s':6,'d':{}}]
                self.index=index
            async def recv(self):
                if self.events:return json.dumps(self.events.pop(0))
                if self.index==0:raise ChannelError('qq_reconnect')
                await asyncio.Event().wait()
            async def send(self,value):
                value=json.loads(value)
                if value['op'] in {2,6}:auth.append(value)
        class Context:
            async def __aenter__(self):
                socket=Socket(len(rounds));rounds.append(socket);return socket
            async def __aexit__(self,*args):pass
        monkeypatch.setattr('websockets.asyncio.client.connect',lambda *a,**kw:Context())
        task=asyncio.create_task(hub.receive())
        try:
            for _ in range(300):
                if len(auth)==2 and hub.connected:break
                await asyncio.sleep(.01)
            assert [item['op'] for item in auth]==[2,6]
            assert auth[0]['d']['intents']==1<<25
            assert auth[1]['d']['session_id']=='engineering-session' and auth[1]['d']['seq']==5
            assert hub.state.db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0]==1
            assert hub.connected
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True);hub.state.close()
    asyncio.run(run())


def test_telegram_auth_logs_redact_url_and_never_replace_existing_webhook(caplog):
    async def run():
        requests=[]
        def respond(request):
            requests.append(request.url.path.rsplit('/',1)[-1])
            method=requests[-1]
            result={'id':123456,'is_bot':True,'username':'engineering_bot'} if method=='getMe' else {'url':'https://existing.test/hook'}
            return httpx.Response(200,json={'ok':True,'result':result})
        api=TelegramAPI({'token':TOKEN},transport=httpx.MockTransport(respond))
        with pytest.raises(ChannelError,match='webhook_active'):await api.verify()
        assert requests==['getMe','getWebhookInfo']
        await api.close()
    with caplog.at_level(logging.INFO,logger='httpx'):asyncio.run(run())
    assert TOKEN not in caplog.text and '[REDACTED]' in caplog.text


def test_telegram_offset_follows_durable_ingest_and_replays_deduplicate(tmp_path,monkeypatch):
    async def run():
        hub=TelegramHub(tmp_path,None,None,configuration={'enabled':True},project=tmp_path)
        class API:
            async def verify(self):return {'username':'engineering_bot'}
            async def updates(self,offset,timeout):
                if offset==0:return [telegram()]
                assert offset==8 and hub.state.db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0]==1
                await asyncio.Event().wait()
            async def close(self):pass
        monkeypatch.setattr('memoria.channels.polling.load_private_json',lambda p:{'token':TOKEN})
        hub.make_api=lambda p:API()
        async def idle():await asyncio.Event().wait()
        hub.run=idle
        await hub.start()
        try:
            for _ in range(100):
                if hub.state.cursor(hub.app)==8:break
                await asyncio.sleep(.001)
            assert hub.state.cursor(hub.app)==8
            assert not hub.state.ingest(hub.app,parse_update(telegram()))
            assert hub.connected
            second=TelegramHub(tmp_path,None,None,configuration={'enabled':True},project=tmp_path)
            await second.start()
            assert second.last_error=='listener_already_running'
        finally:await hub.close()
    asyncio.run(run())


def test_uncertain_telegram_delivery_never_automatically_resends_private_answer(tmp_path):
    async def run():
        hub=TelegramHub(tmp_path,None,None,configuration={},project=tmp_path)
        hub.app='app';hub.state=ChannelState(tmp_path/'channel.db')
        calls=[]
        async def reply(*args):calls.append(args);raise ChannelError('delivery_uncertain')
        hub.api=SimpleNamespace(reply=reply)
        source=parse_update(telegram())
        for _ in range(2):
            with pytest.raises(ChannelError,match='delivery_uncertain'):await hub.send_reply(source,'private engineering reply',0)
        assert len(calls)==1
        hub.state.close()
    asyncio.run(run())


def test_pairing_is_platform_owned(tmp_path):
    first=ChannelState(tmp_path/'qq.db');second=ChannelState(tmp_path/'telegram.db')
    code=first.issue('qq-app','owner-a')
    assert '无效' in second.bind('telegram-app','sender',digest(code['code']))
    assert '绑定成功' in first.bind('qq-app','sender',digest(code['code']))
    assert not second.bound('telegram-app','owner-a')
    first.close();second.close()


@pytest.mark.parametrize('platform',['qq','telegram'])
@pytest.mark.parametrize('notice',[None,False,True,'delivery_failure'])
def test_new_channels_route_only_bound_owner_and_duplicates_do_not_call_reader_again(tmp_path,platform,notice):
    async def run():
        stores={name:Store(tmp_path/(name+'.db'),'json') for name in ('owner-a','owner-b')}
        calls=[];sent=[]
        async def get(user):
            store=stores[user['id']]
            async def turn(session,text,request_id=None):
                calls.append((user['id'],request_id));store.add_message(session,'user',text)
                return {'message':store.add_message(session,'assistant','engineering answer')}
            return SimpleNamespace(state=SimpleNamespace(store=store,chat_turn=turn))
        async def reply(source,text,part):
            if part==-1 and notice=='delivery_failure':raise ChannelError('notice_failed')
            sent.append((source['chat_id'],text,part))
        hub=(QQHub if platform=='qq' else TelegramHub)(tmp_path,
            SimpleNamespace(enabled_user=lambda owner:{'id':owner} if owner in stores else None),
            SimpleNamespace(get=get),configuration={} if notice is None else {'processing_notice':bool(notice)},project=tmp_path)
        hub.app='engineering-app';hub.state=ChannelState(tmp_path/'channel.db');hub.api=SimpleNamespace(reply=reply)
        source=parse_event(qq()) if platform=='qq' else parse_update(telegram())
        code=hub.state.issue(hub.app,'owner-a')
        hub.state.bind(hub.app,source['sender'],digest(code['code']))
        assert hub.state.ingest(hub.app,source)
        row=hub.state.pending(hub.app)[0]
        await hub.handle(row)
        assert calls[0][0]=='owner-a' and calls[0][1].startswith(platform+'-')
        assert stores['owner-a'].db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==2
        assert stores['owner-b'].db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==0
        assert not hub.state.ingest(hub.app,source) and len(calls)==1
        assert sent[-1][1]=='engineering answer'
        assert len(sent)==(2 if notice is True else 1)
        hub.state.close()
        for store in stores.values():store.close()
    asyncio.run(run())


@pytest.mark.parametrize('data',[{'telegram':{'enabled':'true'}},{'qq':{'app_secret':'unused-plaintext'}},{'telegram':{'poll_timeout_seconds':100}},{'telegram':{'proxy_url':'file:///secret'}},{'qq':{'processing_notice':'false'}}])
def test_invalid_bot_config_fails_before_transport(data):
    with pytest.raises(ValueError):validate_bot_configuration(data)


def test_all_channel_routes_require_formal_login_and_never_trust_client_owner(tmp_path):
    source=tmp_path/'isolated.toml';source.write_text('[storage]\ndatabase="'+(tmp_path/'data/memoria.db').as_posix()+'"\n')
    app=create_app(source)
    with TestClient(app) as client:
        for name in ('feishu','qq','telegram'):
            assert client.get('/api/channels/'+name).status_code==401
        login=client.post('/api/auth/register',json={'username':'fixture_owner','password':'engineering-password-2026'})
        assert login.status_code==201
        login=client.post('/api/auth/login',json={'username':'fixture_owner','password':'engineering-password-2026'})
        assert login.status_code==200
        for name in ('feishu','qq','telegram'):
            state=client.get('/api/channels/'+name).json()
            assert not state['configured'] and not state['bound'] and 'token' not in state
            assert client.post('/api/channels/'+name+'/pairing',json={'user_id':'another'}).status_code==503
        assert client.get('/api/channels/unknown').status_code==404
        hub=app.state.channels['qq'];hub.configured=True;hub.app='engineering-qq'
        hub.state=ChannelState(tmp_path/'configured-qq.db')
        assert client.post('/api/channels/qq/pairing',json={'user_id':'another'}).status_code==200
        assert hub.state.db.execute('SELECT owner FROM pairing').fetchone()[0]==login.json()['user']['id']
