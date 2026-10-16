"""QQ official C2C bot REST + resumable WebSocket transport, no QQ password login."""
from __future__ import annotations
import asyncio
import json
import logging
import re
import time
from datetime import datetime
from urllib.parse import quote,urlparse
import httpx
from .errors import ChannelError
from .normalize import packet
from .polling import PollingHub


def parse_event(event):
    if event.get('op')!=0 or event.get('t')!='C2C_MESSAGE_CREATE':return None
    data=event.get('d',{});author=data.get('author',{})
    sender=author.get('user_openid');message_id=data.get('id')
    if author.get('bot') is True:return None
    if not all(isinstance(v,str) and 0<len(v)<=256 for v in (sender,message_id)):return None
    try:
        created=datetime.fromisoformat(data['timestamp'].replace('Z','+00:00'))
        if created.tzinfo is None:return None
    except (ValueError,KeyError,TypeError,AttributeError):return None
    return packet(message_id=message_id,chat_id=sender,sender='qq:'+sender,text=data.get('content',''),
                  created=created.timestamp(),kind='text' if isinstance(data.get('content'),str) else 'unsupported')


def gateway_url(value):
    parsed=urlparse(value)
    if parsed.scheme!='wss' or parsed.hostname not in {'api.bot.qq.com','api.sgroup.qq.com','sandbox.api.sgroup.qq.com'} or parsed.username or parsed.password or parsed.port not in {None,443}:
        raise ChannelError('qq_untrusted_gateway')
    return value


class QQAPI:
    def __init__(self,private,*,proxy=None,transport=None):
        if not re.fullmatch(r'\d{6,20}',str(private.get('app_id',''))) or not isinstance(private.get('app_secret'),str) or len(private['app_secret'])<8:
            raise ChannelError('qq_invalid_credentials_format')
        self.private=private
        self.client=httpx.AsyncClient(base_url='https://api.bot.qq.com',timeout=20,trust_env=False,proxy=proxy or None,transport=transport)
        self.token='';self.expires=0;self.lock=asyncio.Lock()

    async def close(self):await self.client.aclose()

    async def authenticate(self):
        async with self.lock:
            if self.token and self.expires>time.time():return self.token
            try:
                response=await self.client.post('/app/getAppAccessToken',json={'appId':self.private['app_id'],'clientSecret':self.private['app_secret']})
                body=response.json()
            except (httpx.RequestError,ValueError):raise ChannelError('qq_auth_network') from None
            if response.status_code!=200 or not isinstance(body.get('access_token'),str):
                code=body.get('code',response.status_code)
                raise ChannelError('qq_auth_'+str(code) if type(code) is int else 'qq_auth_failed')
            self.token=body['access_token'];self.expires=time.time()+max(1,int(body.get('expires_in',7200))-60)
            return self.token

    async def call(self,method,path,body=None):
        token=await self.authenticate()
        try:
            response=await self.client.request(method,path,headers={'Authorization':'QQBot '+token,'X-Union-Appid':self.private['app_id']},json=body)
            result=response.json()
        except (httpx.RequestError,ValueError):raise ChannelError('qq_network') from None
        if response.status_code>=400 or result.get('code',0)!=0:
            code=result.get('code',response.status_code)
            if response.status_code==401:self.expires=0
            raise ChannelError('qq_'+str(code) if type(code) is int else 'qq_api_error')
        return result

    async def gateway(self):
        body=await self.call('GET','/gateway')
        return gateway_url(body.get('url',''))

    async def reply(self,source,text,part):
        # QQ deduplicates passive replies by msg_id + msg_seq; ack uses seq=1.
        return await self.call('POST','/v2/users/'+quote(source['chat_id'],safe='')+'/messages',
            {'content':text,'msg_type':0,'msg_id':source['message_id'],'msg_seq':part+2})


class QQHub(PollingHub):
    channel='qq'
    label='QQ'
    max_parts=3
    def identity(self,private):return private['app_id']
    def make_api(self,private):return QQAPI(private,proxy=self.configuration.get('proxy_url'))

    async def send_reply(self,source,text,part):
        if time.time()-source['created']>290:raise ChannelError('qq_reply_window_expired')
        await self.api.reply(source,text,part)

    async def receive(self):
        from websockets.asyncio.client import connect
        logger=logging.getLogger('memoria.channels.qq.wire');logger.disabled=True
        session=None;sequence=None;delay=2
        while True:
            self.connected=False
            try:
                endpoint=await self.api.gateway();token=await self.api.authenticate()
                async with connect(endpoint,proxy=self.configuration.get('proxy_url') or None,logger=logger,
                                   ping_interval=None,open_timeout=20,close_timeout=5,max_size=131072) as socket:
                    hello=json.loads(await asyncio.wait_for(socket.recv(),20))
                    if hello.get('op')!=10:raise ChannelError('qq_missing_hello')
                    interval=float(hello['d']['heartbeat_interval'])/1000
                    if not 1<=interval<=120:raise ChannelError('qq_invalid_heartbeat')
                    auth={'token':'QQBot '+token,'session_id':session,'seq':sequence} if session else {'token':'QQBot '+token,'intents':1<<25,'shard':[0,1]}
                    await socket.send(json.dumps({'op':6 if session else 2,'d':auth}))
                    acknowledged=True
                    async def heartbeat():
                        nonlocal acknowledged
                        while True:
                            await asyncio.sleep(interval)
                            if not acknowledged:raise ChannelError('qq_heartbeat_timeout')
                            acknowledged=False
                            await socket.send(json.dumps({'op':1,'d':sequence}))
                    async def consume():
                        nonlocal session,sequence,acknowledged,delay
                        while True:
                            event=json.loads(await socket.recv());op=event.get('op')
                            if op==11:acknowledged=True
                            elif op==1:await socket.send(json.dumps({'op':1,'d':sequence}))
                            elif op==9:
                                session=None;sequence=None
                                raise ChannelError('qq_invalid_session_or_intents')
                            elif op==7:raise ChannelError('qq_reconnect')
                            elif op==0:
                                if event.get('t')=='READY':session=event['d']['session_id']
                                if event.get('t') in {'READY','RESUMED'}:
                                    self.connected=True;self.last_error=None;delay=2
                                source=parse_event(event)
                                if source and self.state.ingest(self.app,source):self.wakeup.set()
                                # A sequence becomes resumable only after durable ingest.
                                sequence=event.get('s',sequence)
                    pending=[asyncio.create_task(heartbeat()),asyncio.create_task(consume())]
                    try:
                        done,_=await asyncio.wait(pending,return_when=asyncio.FIRST_COMPLETED)
                        for task in done:await task
                    finally:
                        for task in pending:task.cancel()
                        await asyncio.gather(*pending,return_exceptions=True)
            except asyncio.CancelledError:raise
            except Exception as exc:
                self.connected=False;self.last_error=exc.code if isinstance(exc,ChannelError) else 'qq_transport_failed'
                await asyncio.sleep(delay);delay=min(delay*2,60)
