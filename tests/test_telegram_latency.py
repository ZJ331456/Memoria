"""Isolated network/progress fixtures, not real bot messages or speed scores."""
import asyncio,json,time
from types import SimpleNamespace
import httpx
import pytest
from memoria.channels.config import validate_bot_configuration
from memoria.channels.telegram import TelegramAPI,TelegramHub,parse_update
from memoria.channels.state import ChannelState,digest

TOKEN='123456:engineering_fixture_token_value'


def source():
    return parse_update({'update_id':7,'message':{'message_id':3,'date':int(time.time()),'text':'工程输入',
        'chat':{'id':123,'type':'private'},'from':{'id':123,'is_bot':False}}})


def test_long_poll_uses_separate_client_and_never_blocks_typing_or_answer():
    async def run():
        polling=asyncio.Event();release=asyncio.Event();seen=[]
        async def respond(request):
            name=request.url.path.rsplit('/',1)[-1];seen.append((name,json.loads(request.content)))
            if name=='getUpdates':polling.set();await release.wait();result=[]
            elif name=='sendChatAction':result=True
            else:result={'message_id':42}
            return httpx.Response(200,json={'ok':True,'result':result})
        api=TelegramAPI({'token':TOKEN},transport=httpx.MockTransport(respond))
        assert api.poll_client is not api.client
        poll=asyncio.create_task(api.updates(8,25));await polling.wait()
        try:
            await asyncio.wait_for(api.typing(source()),.5)
            await asyncio.wait_for(api.reply(source(),'工程答复',0),.5)
            assert not poll.done()
            assert seen[1]==('sendChatAction',{'chat_id':123,'action':'typing'})
            release.set();assert await poll==[]
        finally:
            poll.cancel();await asyncio.gather(poll,return_exceptions=True);await api.close()
        assert api.client.is_closed and api.poll_client.is_closed
    asyncio.run(run())


@pytest.mark.parametrize('mode',['enabled','disabled','failure','blocked','cancelled'])
def test_typing_is_optional_nonblocking_and_stops_with_authorized_turn(tmp_path,mode):
    async def run():
        started=asyncio.Event();release=asyncio.Event();signalled=asyncio.Event();sent=[];signals=[]
        async def typing(packet):
            signals.append(True);signalled.set()
            if mode=='failure':raise RuntimeError('isolated typing failure')
            if mode=='blocked':await asyncio.Event().wait()
        async def reply(packet,text,part):sent.append((text,part))
        async def turn(*args,**kwargs):
            started.set();await release.wait();return {'message':{'content':'工程答复'}}
        async def get(user):
            return SimpleNamespace(state=SimpleNamespace(store=SimpleNamespace(create_session=lambda title:{'id':'session'}),chat_turn=turn))
        hub=TelegramHub(tmp_path,SimpleNamespace(enabled_user=lambda owner:{'id':owner}),SimpleNamespace(get=get),
            configuration={'typing_indicator':mode!='disabled'},project=tmp_path)
        hub.app='app';hub.state=ChannelState(tmp_path/'channel.db');hub.api=SimpleNamespace(typing=typing,reply=reply)
        packet=source();code=hub.state.issue(hub.app,'owner')
        hub.state.bind(hub.app,packet['sender'],digest(code['code']));hub.state.ingest(hub.app,packet)
        row=hub.state.pending(hub.app)[0];hub.state.claim(hub.app,row['message_id'])
        task=asyncio.create_task(hub.handle(row));await started.wait()
        try:
            if mode!='disabled':await asyncio.wait_for(signalled.wait(),.5)
            if mode=='cancelled':
                task.cancel()
                with pytest.raises(asyncio.CancelledError):await task
                assert sent==[]
            else:
                release.set();await asyncio.wait_for(task,.5)
                assert sent==[('工程答复',0)]
            assert bool(signals)==(mode!='disabled')
            assert not [t for t in asyncio.all_tasks() if t.get_name()=='telegram-typing' and not t.done()]
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True);hub.state.close()
    asyncio.run(run())


def test_unbound_or_changed_binding_never_gets_typing_signal(tmp_path):
    async def run():
        signals=[]
        async def typing(packet):signals.append(True)
        hub=TelegramHub(tmp_path,SimpleNamespace(enabled_user=lambda owner:{'id':owner}),None,configuration={},project=tmp_path)
        hub.app='app';hub.state=ChannelState(tmp_path/'channel.db');hub.api=SimpleNamespace(typing=typing)
        async with hub.processing_feedback(source(),'owner'):await asyncio.sleep(.01)
        assert signals==[]
        code=hub.state.issue(hub.app,'owner');hub.state.bind(hub.app,source()['sender'],digest(code['code']))
        async with hub.processing_feedback({**source(),'binding_created':0},'owner'):await asyncio.sleep(.01)
        assert signals==[];hub.state.close()
    asyncio.run(run())


def test_receiving_wakes_dispatch_without_waiting_for_periodic_scan(tmp_path):
    async def run():
        hub=TelegramHub(tmp_path,None,None,configuration={},project=tmp_path)
        called=asyncio.Event()
        hub.dispatch_pending=lambda:called.set()
        task=asyncio.create_task(hub.run())
        try:
            await called.wait();called.clear();hub.wakeup.set()
            await asyncio.wait_for(called.wait(),.3)
        finally:task.cancel();await asyncio.gather(task,return_exceptions=True)
    asyncio.run(run())


def test_latency_is_persisted_deduplicated_owner_scoped_and_deleted_with_inbox(tmp_path,monkeypatch):
    state=ChannelState(tmp_path/'channel.db');packet=source();packet['created']=100
    code=state.issue('app','owner');state.bind('app',packet['sender'],digest(code['code']))
    clock=[101];monkeypatch.setattr('memoria.channels.state.time.time',lambda:clock[0])
    assert state.ingest('app',packet)
    clock[0]=102;assert state.claim('app',packet['message_id'])
    clock[0]=103;state.mark_timing('app',packet['message_id'],'generation_started_at')
    clock[0]=107;state.update('app',packet['message_id'],'generated',reply='工程结果')
    clock[0]=108;state.update('app',packet['message_id'],'sent')
    assert not state.ingest('app',packet)
    stats=state.latency('app','owner')
    assert stats['samples']==1 and stats['receive_after_platform_timestamp_ms']==1000
    assert stats['queue_and_initialization_ms']==2000 and stats['agent_ms']==4000 and stats['delivery_ms']==1000
    assert state.latency('app','other-owner')['samples']==0
    assert 'message_id' not in stats and 'source' not in stats
    with state.db:state.db.execute('DELETE FROM inbox')
    assert state.db.execute('SELECT COUNT(*) FROM channel_timings').fetchone()[0]==0
    state.close()


def test_typing_indicator_requires_boolean_and_does_not_apply_to_other_platforms():
    validate_bot_configuration({'telegram':{'typing_indicator':False}})
    for value in ({'telegram':{'typing_indicator':'true'}},{'qq':{'typing_indicator':True}}):
        with pytest.raises(ValueError):validate_bot_configuration(value)
