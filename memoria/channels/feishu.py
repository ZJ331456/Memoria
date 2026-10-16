"""One app listener, explicit per-person binding, durable and bounded delivery."""
from __future__ import annotations
import asyncio
import importlib.util
import json
import multiprocessing
import os
import queue
import time
from contextlib import suppress
from pathlib import Path
from ..private_files import load_private_json
from .feishu_api import FeishuAPI, FeishuError, reply_parts
from .feishu_state import FeishuState, digest
from .feishu_transport import listen

HELP='请在本地Chat点击“连接飞书”，登录自己的账号后生成绑定码，再在此私聊发送 /绑定 绑定码。绑定后直接发送文字即可；/new 新对话，/stop 停止，/unbind 解绑。'


class FeishuHub:
    def __init__(self,root:Path,accounts,pool,*,config_directory:Path):
        self.root,self.accounts,self.pool=root,accounts,pool
        self.credentials_file=config_directory/'feishu-bot.credentials.dpapi'
        self.state_file=root/'channels/feishu.db'
        self.state=None;self.api=None;self.app='';self.process=None;self.worker=None
        self.connected=False;self.last_error=None;self.tasks={};self.stopping=False
        self.queue=None;self.stop_signal=None;self.last_heartbeat=0;self.lease=None
        config=config_directory/'feishu-bot.json'
        self.configured=False
        if config.is_file():
            try:self.configured=json.loads(config.read_text(encoding='utf-8-sig')).get('enabled') is True
            except (ValueError,OSError):self.last_error='invalid_configuration'

    async def start(self):
        if not self.configured:return
        try:
            private=load_private_json(self.credentials_file)
            self.state_file.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            self.lease=open(self.state_file.with_suffix('.lock'),'a+b')
            self.lease.seek(0);self.lease.write(b'0');self.lease.flush();self.lease.seek(0)
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(self.lease.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(self.lease.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:self.lease.close();self.lease=None;self.last_error='listener_already_running';return
            self.app=digest(private['app_id']);self.state=FeishuState(self.state_file);self.state.recover(self.app)
            self.api=FeishuAPI({'app_id':private['app_id'],'app_secret':private['app_secret']})
            if not importlib.util.find_spec('lark_oapi'):
                self.last_error='sdk_missing';return
            context=multiprocessing.get_context('spawn')
            self.queue=context.Queue(maxsize=8);self.stop_signal=context.Event()
            self.process=context.Process(target=listen,args=(str(self.credentials_file),str(self.state_file),self.queue,self.stop_signal),daemon=True,name='memoria-feishu-ws')
            self.process.start()
            self.worker=asyncio.create_task(self.run(),name='feishu-inbox')
        except Exception:
            self.last_error='startup_failed'  # No raw exceptions, IDs, credentials or WS URLs.

    def status(self,owner:str)->dict:
        connected=self.connected and time.time()-self.last_heartbeat<5 and bool(self.process and self.process.is_alive())
        return {'configured':self.configured,'connected':connected,'bound':bool(self.state and self.state.bound(self.app,owner)),
                'last_error':self.last_error,'mode':'p2p_text','pairing_ttl_seconds':600}

    def pairing(self,owner:str)->dict:
        if not self.state or not self.configured:raise ValueError('飞书机器人尚未由本机管理员配置')
        return self.state.issue(self.app,owner)

    def unbind(self,owner:str):
        if not self.state:return
        self.state.unbind(self.app,owner)
        for item in list(self.tasks.values()):
            if item['owner']==owner:item['task'].cancel()

    async def close(self):
        self.stopping=True
        if self.worker:self.worker.cancel()
        if self.worker:
            with suppress(asyncio.CancelledError):await self.worker
        active=[item['task'] for item in self.tasks.values()]
        for task in active:task.cancel()
        if active:await asyncio.gather(*active,return_exceptions=True)
        if self.stop_signal:self.stop_signal.set()
        if self.process:
            if self.process.is_alive():self.process.terminate()
            await asyncio.to_thread(self.process.join,2)
            if self.process.is_alive():self.process.kill();await asyncio.to_thread(self.process.join,2)
        if self.queue:self.queue.close();self.queue.cancel_join_thread()
        if self.api:await self.api.close()
        if self.state:self.state.close()
        if self.lease:self.lease.close();self.lease=None
        self.connected=False

    async def run(self):
        while True:
            while True:
                try:
                    status=self.queue.get_nowait();self.connected=status.get('connected',False);self.last_heartbeat=time.time()
                    if status.get('error'):self.last_error=status['error']
                except queue.Empty:break
            if self.process and not self.process.is_alive():self.connected=False;self.last_error='transport_stopped'
            for row in self.state.pending(self.app):
                sender=row['sender'];packet=json.loads(row['body'])
                if (packet.get('text','').strip().lower() in {'/stop','/停止'} and sender in self.tasks
                        and row['message_id']!=self.tasks[sender]['message_id'] and row['owner']==self.tasks[sender]['owner']):
                    self.tasks[sender]['task'].cancel()
                if sender in self.tasks or len(self.tasks)>=4:continue
                if row['status']=='received' and not self.state.claim(self.app,row['message_id']):continue
                binding=self.state.binding(self.app,sender)
                task=asyncio.create_task(self.handle(row),name='feishu-turn')
                self.tasks[sender]={'task':task,'owner':binding['owner'] if binding else None,'message_id':row['message_id']}
                task.add_done_callback(lambda done,key=sender:self.finished(key,done))
            await asyncio.sleep(0.4)

    def finished(self,key,task):
        self.tasks.pop(key,None)
        if not task.cancelled():
            with suppress(Exception):task.result()

    async def handle(self,row:dict):
        message_id=row['message_id'];packet=json.loads(row['body']);sender=packet['sender']
        try:
            binding=self.state.binding(self.app,sender)
            if row['status'] in {'generated','delivery_failed'}:
                await self.deliver(row,packet);return
            text=packet.get('text','').strip();command=text.lower()
            owner=None;session=None
            if packet.get('pair_hash'):
                try:reply=self.state.bind(self.app,sender,packet['pair_hash'])
                except ValueError:reply='绑定尝试过于频繁，请十分钟后再试。'
            elif command in {'/help','/帮助'}:reply=HELP
            elif not binding:reply=HELP
            elif row.get('owner')!=binding['owner'] or packet.get('binding_created')!=binding['created']:
                reply='这条消息的账号绑定已变化，请重新发送。'
            else:
                owner=binding['owner'];user=self.accounts.enabled_user(owner)
                if not user:
                    self.state.unbind(self.app,owner);reply='本地账号当前不可用，请登录本机确认。';owner=None
                elif command in {'/unbind','/解绑'}:
                    self.state.unbind(self.app,owner);reply='已解除绑定；已有聊天记录仍保留在原Memoria账号。';owner=None
                elif command in {'/new','/新对话'}:
                    self.state.session(self.app,sender,owner,None);reply='已切换到新对话，发送下一条文字开始。'
                elif command in {'/stop','/停止'}:
                    self.state.cancel_pending(self.app,sender,message_id);reply='已停止当前生成并清空待处理消息；已保存内容可在本地查看。'
                elif packet['kind']!='text':reply='目前支持机器人私聊文字消息，请发送文字。'
                elif packet['created']<binding['created']-5 or packet['created']<time.time()-86400:
                    reply='这条消息早于当前绑定或过旧，请重新发送。'
                elif not self.state.allowed_turn(self.app,sender):reply='十分钟内对话次数已达到30条，请稍后继续。'
                else:
                    await self.api.reply(message_id,'已收到，正在处理。可发送 /stop 停止本轮。',-1)
                    tenant=await self.pool.get(user)
                    # Recheck ownership after slow tenant/MCP initialization.
                    current=self.state.binding(self.app,sender)
                    if not current or current['owner']!=owner:return self.state.update(self.app,message_id,'ignored')
                    session=binding['session']
                    if not session or not tenant.state.store.session(session):
                        session=tenant.state.store.create_session('飞书 · '+text[:30])['id']
                        self.state.session(self.app,sender,owner,session)
                    self.state.update(self.app,message_id,'processing',owner=owner,session=session)
                    result=await tenant.state.chat_turn(session,text,request_id='feishu-'+digest(message_id)[:24])
                    reply=result['message']['content']
            self.state.update(self.app,message_id,'generated',reply=reply,owner=owner,session=session)
            await self.deliver({**row,'reply':reply,'owner':owner},packet)
        except asyncio.CancelledError:
            self.state.update(self.app,message_id,'interrupted',error='turn_cancelled');raise
        except Exception:
            self.last_error='turn_failed'
            reply='本轮处理失败，请在本地Studio核对模型配置和运行记录后重试；系统不会自动重复调用模型。'
            self.state.update(self.app,message_id,'generated',reply=reply,error='turn_failed')
            await self.deliver({**row,'reply':reply,'owner':None},packet)

    async def deliver(self,row:dict,packet:dict):
        try:
            for number,part in enumerate(reply_parts(row['reply'])):
                if row.get('owner'):
                    binding=self.state.binding(self.app,packet['sender'])
                    if not binding or binding['owner']!=row['owner'] or not self.accounts.enabled_user(row['owner']):
                        self.state.update(self.app,row['message_id'],'ignored');return
                await self.api.reply(row['message_id'],part,number)
            self.state.update(self.app,row['message_id'],'sent');self.last_error=None
        except Exception as exc:
            code=exc.code if isinstance(exc,FeishuError) else 'delivery_network'
            self.last_error=code;self.state.delivery_failed(self.app,row['message_id'],code)
