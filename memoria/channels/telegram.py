"""Official Telegram Bot API, durable long polling and private account binding."""
from __future__ import annotations
import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager,suppress
import httpx
from .errors import ChannelError
from .normalize import packet
from .polling import PollingHub


class TokenURLFilter(logging.Filter):
    def filter(self,record):
        value=record.getMessage()
        record.msg=re.sub(r'(/bot)\d{5,}:[A-Za-z0-9_-]+',r'\1[REDACTED]',value)
        record.args=()
        return True


def protect_logs():
    for name in ('httpx','httpcore.connection','httpcore.http11','httpcore.http2','httpcore.proxy'):
        logger=logging.getLogger(name)
        if not any(isinstance(f,TokenURLFilter) for f in logger.filters):logger.addFilter(TokenURLFilter())


def parse_update(update):
    message=update.get('message')
    if not isinstance(message,dict):return None
    chat,author=message.get('chat',{}),message.get('from',{})
    if chat.get('type')!='private' or author.get('is_bot') is not False:return None
    if type(chat.get('id')) is not int or type(author.get('id')) is not int or chat['id']<=0 or chat['id']!=author['id']:return None
    if type(message.get('message_id')) is not int or type(message.get('date')) is not int:return None
    return packet(message_id=str(chat['id'])+':'+str(message['message_id']),chat_id=str(chat['id']),
                  sender='telegram:'+str(author['id']),text=message.get('text',''),created=message['date'],
                  kind='text' if isinstance(message.get('text'),str) else 'unsupported',
                  external_message_id=message['message_id'])


class TelegramAPI:
    def __init__(self,private,*,proxy=None,transport=None):
        token=private.get('token','')
        if not re.fullmatch(r'\d{5,}:[A-Za-z0-9_-]{20,}',token):raise ChannelError('invalid_token_format')
        self.token=token
        protect_logs()
        options=dict(base_url='https://api.telegram.org',trust_env=False,proxy=proxy or None,transport=transport)
        self.client=httpx.AsyncClient(**options,timeout=httpx.Timeout(15,connect=5,pool=2),
            limits=httpx.Limits(max_connections=8,max_keepalive_connections=4,keepalive_expiry=90))
        self.poll_client=httpx.AsyncClient(**options,timeout=httpx.Timeout(40,connect=5,pool=2),
            limits=httpx.Limits(max_connections=1,max_keepalive_connections=1,keepalive_expiry=90))

    async def close(self):
        await self.client.aclose();await self.poll_client.aclose()

    async def call(self,method,body=None):
        if method not in {'getMe','getWebhookInfo','getUpdates','sendMessage','sendChatAction'}:raise ChannelError('unsupported_method')
        try:
            client=self.poll_client if method=='getUpdates' else self.client
            response=await client.post('/bot'+self.token+'/'+method,json=body or {})
        except httpx.RequestError:
            raise ChannelError('delivery_uncertain' if method=='sendMessage' else 'telegram_network') from None
        try:payload=response.json()
        except ValueError:raise ChannelError('telegram_invalid_response') from None
        if response.status_code!=200 or payload.get('ok') is not True:
            code=payload.get('error_code',response.status_code)
            raise ChannelError('telegram_'+str(code) if type(code) is int else 'telegram_api_error')
        return payload['result']

    async def verify(self):
        me=await self.call('getMe')
        if me.get('is_bot') is not True or not isinstance(me.get('username'),str):raise ChannelError('telegram_not_bot')
        if str(me.get('id'))!=self.token.split(':',1)[0]:raise ChannelError('telegram_identity_mismatch')
        info=await self.call('getWebhookInfo')
        if info.get('url'):raise ChannelError('telegram_webhook_active')
        return me

    async def updates(self,offset,timeout):
        return await self.call('getUpdates',dict(offset=offset,timeout=timeout,limit=32,allowed_updates=['message']))

    async def reply(self,source,text,part):
        return await self.call('sendMessage',dict(chat_id=int(source['chat_id']),text=text,
            reply_parameters={'message_id':source['external_message_id'],'allow_sending_without_reply':True},
            link_preview_options={'is_disabled':True}))

    async def typing(self,source):
        return await self.call('sendChatAction',{'chat_id':int(source['chat_id']),'action':'typing'})


class TelegramHub(PollingHub):
    channel='telegram'
    label='Telegram'
    def identity(self,private):return private['token'].split(':',1)[0]
    def make_api(self,private):return TelegramAPI(private,proxy=self.configuration.get('proxy_url'))

    @asynccontextmanager
    async def processing_feedback(self,source,owner):
        async def signal():
            while True:
                binding=self.state.binding(self.app,source['sender'])
                if (not binding or binding['owner']!=owner or binding['created']!=source.get('binding_created')
                        or not self.accounts.enabled_user(owner)):return
                started=time.monotonic()
                delay=4
                try:await asyncio.wait_for(self.api.typing(source),2)
                except asyncio.CancelledError:raise
                except Exception as exc:
                    if isinstance(exc,ChannelError) and exc.code in {'telegram_429','telegram_401','telegram_403'}:return
                    delay=8
                await asyncio.sleep(max(.1,delay-(time.monotonic()-started)))
        task=asyncio.create_task(signal(),name='telegram-typing') if self.configuration.get('typing_indicator',True) else None
        try:yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError,Exception):await task

    async def receive(self):
        delay=2
        while True:
            try:
                me=await self.api.verify()
                expected=self.configuration.get('expected_username','')
                if expected and me['username'].lower()!=expected.lstrip('@').lower():raise ChannelError('telegram_username_mismatch')
                self.connected=True;self.last_error=None;delay=2
                while True:
                    updates=await self.api.updates(self.state.cursor(self.app),int(self.configuration.get('poll_timeout_seconds',25)))
                    if not isinstance(updates,list):raise ChannelError('telegram_invalid_updates')
                    for update in updates:
                        if type(update.get('update_id')) is not int:raise ChannelError('telegram_invalid_update_id')
                        source=parse_update(update)
                        if source and self.state.ingest(self.app,source):self.wakeup.set()
                        # Acknowledge only after durable ingest (or explicit filtering).
                        self.state.advance(self.app,update['update_id']+1)
            except asyncio.CancelledError:raise
            except Exception as exc:
                self.connected=False;self.last_error=exc.code if isinstance(exc,ChannelError) else 'telegram_transport_failed'
                if self.last_error in {'telegram_401','telegram_webhook_active','telegram_username_mismatch'}:return
                await asyncio.sleep(delay);delay=min(delay*2,60)

    async def send_reply(self,source,text,part):
        state=self.state.delivery_status(self.app,source['message_id'],part)
        if state=='sent':return
        if state in {'sending','uncertain'}:raise ChannelError('delivery_uncertain')
        self.state.delivery_part(self.app,source['message_id'],part,'sending')
        try:await self.api.reply(source,text,part)
        except ChannelError as exc:
            self.state.delivery_part(self.app,source['message_id'],part,'uncertain' if exc.code=='delivery_uncertain' else 'retryable')
            raise
        self.state.delivery_part(self.app,source['message_id'],part,'sent')
