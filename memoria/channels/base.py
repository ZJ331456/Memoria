"""Shared account-bound channel turns; adapters only handle platform I/O."""
from __future__ import annotations
import asyncio,json,os,time
from contextlib import suppress
from pathlib import Path
from .feishu_api import reply_parts
from .state import digest


from .errors import ChannelError


class ChannelHub:
    channel='feishu'
    label='飞书'
    max_parts=12
    @property
    def help_text(self):
        return '请在本地Chat的账号菜单打开“通讯连接”，选择'+self.label+'，生成绑定码；然后在此私聊发送 /绑定 绑定码。绑定后直接发送文字；/new 新对话，/stop 停止，/unbind 解绑。'

    def acquire(self):
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
        except OSError:
            self.lease.close();self.lease=None
            raise ChannelError('listener_already_running')

    def dispatch_pending(self):
        for row in self.state.pending(self.app):
            sender=row['sender'];packet=json.loads(row['body'])
            if (packet.get('text','').strip().lower() in {'/stop','/停止'} and sender in self.tasks
                    and row['message_id']!=self.tasks[sender]['message_id'] and row['owner']==self.tasks[sender]['owner']):
                self.tasks[sender]['task'].cancel()
            if sender in self.tasks or len(self.tasks)>=4:continue
            if row['status']=='received' and not self.state.claim(self.app,row['message_id']):continue
            binding=self.state.binding(self.app,sender)
            task=asyncio.create_task(self.handle(row),name=self.channel+'-turn')
            self.tasks[sender]={'task':task,'owner':binding['owner'] if binding else None,'message_id':row['message_id']}
            task.add_done_callback(lambda done,key=sender:self.finished(key,done))

    async def send_reply(self,packet,text,part):
        await self.api.reply(packet['message_id'],text,part)

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
            elif command in {'/help','/帮助','/start'}:reply=self.help_text
            elif not binding:reply=self.help_text
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
                    if self.configuration.get('processing_notice',False):
                        with suppress(Exception):
                            await self.send_reply(packet,'已收到，正在处理。可发送 /stop 停止本轮。',-1)
                    tenant=await self.pool.get(user)
                    # Recheck ownership after slow tenant/MCP initialization.
                    current=self.state.binding(self.app,sender)
                    if not current or current['owner']!=owner:return self.state.update(self.app,message_id,'ignored')
                    session=binding['session']
                    if not session or not tenant.state.store.session(session):
                        session=tenant.state.store.create_session(self.label+' · '+text[:30])['id']
                        self.state.session(self.app,sender,owner,session)
                    self.state.update(self.app,message_id,'processing',owner=owner,session=session)
                    async with self.turn_slots:
                        # Binding can change while waiting for the global model slot.
                        current=self.state.binding(self.app,sender)
                        if not current or current['owner']!=owner or current['created']!=binding['created'] or not self.accounts.enabled_user(owner):
                            self.state.update(self.app,message_id,'ignored');return
                        result=await tenant.state.chat_turn(session,text,request_id=self.channel+'-'+digest(message_id)[:24])
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
            parts=reply_parts(row['reply'])
            if len(parts)>self.max_parts:
                parts=parts[:self.max_parts];parts[-1]+='\n（完整回复请在本地Chat查看）'
            for number,part in enumerate(parts):
                if row.get('owner'):
                    binding=self.state.binding(self.app,packet['sender'])
                    if not binding or binding['owner']!=row['owner'] or (packet.get('binding_created') is not None and binding['created']!=packet['binding_created']) or not self.accounts.enabled_user(row['owner']):
                        self.state.update(self.app,row['message_id'],'ignored');return
                await self.send_reply(packet,part,number)
            self.state.update(self.app,row['message_id'],'sent');self.last_error=None
        except Exception as exc:
            code=exc.code if isinstance(exc,ChannelError) else 'delivery_network'
            if code=='delivery_uncertain':
                self.last_error=code;self.state.update(self.app,row['message_id'],'failed',error=code);return
            self.last_error=code;self.state.delivery_failed(self.app,row['message_id'],code)
