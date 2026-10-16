"""One app listener, explicit per-person binding, durable and bounded delivery."""
from __future__ import annotations
import asyncio
import importlib.util
import multiprocessing
import os
import queue
import time
from contextlib import suppress
from pathlib import Path
from ..private_files import load_private_json
from .feishu_api import FeishuAPI
from .state import ChannelState as FeishuState, digest
from .base import ChannelHub
from .feishu_transport import listen

class FeishuHub(ChannelHub):
    def __init__(self,root:Path,accounts,pool,*,configuration:dict,project:Path):
        self.root,self.accounts,self.pool=root,accounts,pool
        self.configuration=configuration
        self.turn_slots=asyncio.Semaphore(4)
        self.credentials_file=Path(configuration.get('credentials_file',str(root/'secrets/feishu-bot.credentials.dpapi')))
        if not self.credentials_file.is_absolute():self.credentials_file=project/self.credentials_file
        self.state_file=root/'channels/feishu.db'
        self.state=None;self.api=None;self.app='';self.process=None;self.worker=None
        self.connected=False;self.last_error=None;self.tasks={};self.stopping=False
        self.queue=None;self.stop_signal=None;self.last_heartbeat=0;self.lease=None
        self.configured=configuration.get('enabled',False) is True

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
            self.dispatch_pending()
            await asyncio.sleep(0.4)
