"""Bounded background transport lifecycle, independent of HTTP readiness."""
import asyncio
from pathlib import Path
from .base import ChannelHub
from .errors import ChannelError
from .state import ChannelState,digest
from ..private_files import load_private_json


class PollingHub(ChannelHub):
    def __init__(self,root,accounts,pool,*,configuration,project):
        self.root,self.accounts,self.pool=root,accounts,pool
        self.turn_slots=asyncio.Semaphore(4)
        self.configuration=configuration
        self.credentials_file=Path(configuration.get('credentials_file',str(root/'secrets'/(self.channel+'-bot.credentials.dpapi'))))
        if not self.credentials_file.is_absolute():self.credentials_file=project/self.credentials_file
        self.state_file=root/'channels'/(self.channel+'.db')
        self.configured=configuration.get('enabled',False) is True
        self.state=None;self.api=None;self.app='';self.worker=None;self.receiver=None;self.lease=None
        self.connected=False;self.last_error=None;self.tasks={};self.stopping=False

    def status(self,owner):
        return dict(configured=self.configured,connected=self.connected,bound=bool(self.state and self.state.bound(self.app,owner)),
                    last_error=self.last_error,mode='private_text',pairing_ttl_seconds=600)

    def pairing(self,owner):
        if not self.state or not self.configured:raise ValueError('该通讯机器人尚未由本机管理员配置')
        return self.state.issue(self.app,owner)

    def unbind(self,owner):
        if not self.state:return
        self.state.unbind(self.app,owner)
        for item in list(self.tasks.values()):
            if item['owner']==owner:item['task'].cancel()

    async def start(self):
        if not self.configured:return
        try:
            self.acquire()
            private=load_private_json(self.credentials_file)
            self.api=self.make_api(private)
            self.app=digest(self.channel+':'+self.identity(private))
            self.state=ChannelState(self.state_file);self.state.recover(self.app)
            self.receiver=asyncio.create_task(self.receive(),name=self.channel+'-receiver')
            self.worker=asyncio.create_task(self.run(),name=self.channel+'-inbox')
        except Exception as exc:
            self.last_error=exc.code if isinstance(exc,ChannelError) else 'startup_failed'
            await self.close()

    async def run(self):
        while True:
            self.dispatch_pending()
            await asyncio.sleep(.4)

    async def close(self):
        self.stopping=True
        pending=[task for task in (self.receiver,self.worker) if task]+[row['task'] for row in self.tasks.values()]
        for task in pending:task.cancel()
        if pending:await asyncio.gather(*pending,return_exceptions=True)
        if self.api:await self.api.close()
        if self.state:self.state.close();self.state=None
        if self.lease:self.lease.close();self.lease=None
        self.connected=False
