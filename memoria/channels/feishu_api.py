"""Minimal bot REST calls against a fixed official HTTPS origin."""
from __future__ import annotations
import asyncio
import json
import time
import uuid
from urllib.parse import quote
import httpx


from .errors import ChannelError


class FeishuError(ChannelError):
    pass


def reply_parts(text:str,maximum=3500)->list[str]:
    # Bound serialized UTF-8 text comfortably below Feishu's text-message limit.
    truncated=len(text)>20000
    text=text[:20000]
    parts=[];current='';size=0
    for char in text:
        length=len(json.dumps(char,ensure_ascii=False).encode())-2
        if size+length>maximum:parts.append(current);current='';size=0
        current+=char;size+=length
    if current:parts.append(current)
    if len(parts)>12:parts=parts[:12];truncated=True
    if truncated:parts[-1]=parts[-1][:-100]+'\n（完整回复请在本地Chat查看）'
    return parts or ['本轮没有可发送的回复，请在本地查看记录。']


class FeishuAPI:
    def __init__(self,private:dict,transport=None):
        self.private=private
        self.client=httpx.AsyncClient(base_url='https://open.feishu.cn',timeout=20,trust_env=False,transport=transport)
        self.token='';self.expires=0;self.lock=asyncio.Lock()

    async def close(self):await self.client.aclose()

    async def authenticate(self):
        async with self.lock:
            if self.token and self.expires>time.time():return self.token
            response=await self.client.post('/open-apis/auth/v3/tenant_access_token/internal',json=self.private)
            if response.status_code!=200:raise FeishuError(response.status_code)
            payload=response.json()
            if payload.get('code')!=0:raise FeishuError(payload.get('code','invalid_response'))
            self.token=payload['tenant_access_token'];self.expires=time.time()+max(1,payload.get('expire',7200)-120)
            return self.token

    async def reply(self,message_id:str,text:str,part:int):
        token=await self.authenticate()
        body={'msg_type':'text','content':json.dumps({'text':text},ensure_ascii=False),
              'uuid':str(uuid.uuid5(uuid.NAMESPACE_URL,message_id+':memoria:'+str(part)))}
        response=await self.client.post('/open-apis/im/v1/messages/'+quote(message_id,safe='')+'/reply',
                                        headers={'Authorization':'Bearer '+token},json=body)
        if response.status_code!=200:raise FeishuError(response.status_code)
        payload=response.json()
        if payload.get('code')!=0:
            if payload.get('code') in {99991663,99991664,99991668}:self.expires=0
            raise FeishuError(payload.get('code','invalid_response'))
