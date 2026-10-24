"""Full request bounds. Remote bytes never pretend to be exact provider tokens."""
from __future__ import annotations
import asyncio
import copy
import json
import hashlib
import httpx
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..llm.types import ContextLengthError
from .assembler import compact_context_frame, minimum_context_frame_chars
from .budget import ContextBudget


@lru_cache(maxsize=4)
def local_assets(path):
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False)
    config=json.loads((Path(path)/'config.json').read_text(encoding='utf-8'))
    maximum=config.get('max_position_embeddings',config.get('text_config',{}).get('max_position_embeddings',8192))
    if type(maximum) is not int or maximum < 2:
        raise ValueError('invalid local model context metadata')
    files=sorted({*Path(path).glob('tokenizer*'),*Path(path).glob('*tokens*.json'),*Path(path).glob('vocab*'),*Path(path).glob('merges*'),*Path(path).glob('chat_template*'),Path(path)/'config.json'})
    digest=hashlib.sha256()
    for file in files:
        if file.is_file():
            digest.update(file.name.encode());digest.update(file.read_bytes())
    return tokenizer,maximum,digest.hexdigest()


def count_local_path(path,messages,tools,context_cap=None):
    from ..llm.backends.process_engine import render_chat_prompt
    tokenizer,limit,fingerprint=local_assets(str(path))
    text=render_chat_prompt(tokenizer,messages,tools)
    # No hidden 8K default: a local model is bounded by its own config unless the
    # caller (HTTP counter endpoint) passes an explicit cap.
    return len(tokenizer(text)['input_ids']),(min(context_cap,limit) if context_cap is not None else limit),fingerprint


def local_count(selected,messages,tools):
    from ..llm.backends.process_engine import model_path,render_chat_prompt
    tokens,limit,_=count_local_path(model_path(selected.model),messages,tools)
    return tokens,limit


@dataclass
class PreparedReader:
    messages: list
    tools: list | None
    model: object
    max_tokens: int
    report: dict


class ReaderBudget:
    def __init__(self,settings,token_counter=None,transport=None):
        self.settings,self.token_counter=settings,token_counter
        self.transport=transport
        if type(settings.reader_input_bytes) is not int or settings.reader_input_bytes < 256:
            raise ValueError('reader_input_bytes must be an integer >=256')
        if settings.reader_context_tokens is not None and (type(settings.reader_context_tokens) is not int or settings.reader_context_tokens < 2):
            raise ValueError('reader_context_tokens must be a positive integer or unset')
        if type(settings.reader_safety_tokens) is not int or settings.reader_safety_tokens < 0:
            raise ValueError('reader_safety_tokens must be a nonnegative integer')
        if type(getattr(settings,'reader_local_count',False)) is not bool:
            raise ValueError('reader_local_count must be boolean')

    async def http_local_count(self,selected,payload):
        try:
            async with httpx.AsyncClient(transport=self.transport,trust_env=False,timeout=20,follow_redirects=False) as client:
                response=await client.post(selected.base_url.rstrip('/')+'/reader-token-count',json=payload,headers=selected.auth_headers())
            if response.status_code!=200: raise ValueError('counter unavailable')
            data=response.json()
            if data.get('protocol')!='memoria-chat-template-v1' or data.get('model')!=selected.model:
                raise ValueError('counter binding mismatch')
            if type(data.get('input_tokens')) is not int or data['input_tokens']<0 or type(data.get('context_tokens')) is not int or data['context_tokens']<2:
                raise ValueError('invalid token counts')
            fingerprint=data.get('tokenizer_sha256','')
            if len(fingerprint)!=64 or any(c not in '0123456789abcdef' for c in fingerprint): raise ValueError('missing tokenizer binding')
            return data['input_tokens'],data['context_tokens'],fingerprint
        except Exception as exc:
            raise ContextLengthError('本地HTTP reader计数服务或模型绑定不可核验；未发送生成请求') from exc

    async def measure(self,selected,messages,tools,max_tokens,stream=False,**generation):
        from ..llm.backends.openai_http import OpenAICompatBackend
        if type(max_tokens) is not int or max_tokens < 1:
            raise ValueError('output reservation must be a positive integer')
        payload=OpenAICompatBackend.payload(selected,messages,max_tokens,tools,stream,**generation)
        size=len(json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8'))
        tokens,limit,fingerprint=None,None,None
        if selected.is_local_process():
            if self.token_counter:
                tokens,limit=self.token_counter(selected,messages,tools)
            else:
                try:
                    tokens,limit=await asyncio.to_thread(local_count,selected,messages,tools)
                except Exception as exc:
                    raise ContextLengthError('本地reader聊天tokenizer或窗口元数据不可核验；未发送模型请求') from exc
            if self.settings.reader_context_tokens is not None:
                limit=min(limit,self.settings.reader_context_tokens)
        elif selected.is_local_http() and getattr(self.settings,'reader_local_count',False):
            tokens,limit,fingerprint=await self.http_local_count(selected,payload)
            if self.settings.reader_context_tokens is not None:
                limit=min(limit,self.settings.reader_context_tokens)
        elif self.settings.reader_context_tokens is not None:
            raise ContextLengthError('远程/HTTP reader没有可核验聊天tokenizer，不能启用精确Token窗口；请使用明确字节预算')
        safety=self.settings.reader_safety_tokens
        return {'input_bytes':size,'input_byte_limit':self.settings.reader_input_bytes,
                'input_tokens':tokens,'context_tokens':limit,'reserved_output_tokens':max_tokens,
                'safety_tokens':safety if tokens is not None else None,'exact_input_tokens':tokens is not None,
                'fits':size<=self.settings.reader_input_bytes and (tokens is None or tokens+max_tokens+safety<=limit),
                'scope':'all messages/roles/history/tool calls/results/tool schemas/generation options',
                'provider_window_verified':tokens is not None,'remote_token_count':'unknown' if tokens is None else None,
                'tokenizer_sha256':fingerprint,'token_count_origin':'local_http' if fingerprint else 'process_local' if tokens is not None else 'request_bytes'}

    async def check(self,selected,messages,tools,max_tokens,stream=False,**generation):
        report=await self.measure(selected,messages,tools,max_tokens,stream,**generation)
        if not report['fits']:
            raise ContextLengthError('完整reader请求及输出预留超过配置预算；未发送模型请求')
        return report

    async def prepare(self,messages,tools=None,*,selected=None,max_tokens=None,stream=False,atomic=False,**generation):
        model=copy.deepcopy(selected or self.settings.main)
        output=max_tokens if max_tokens is not None else self.settings.max_tokens
        prepared,schemas=copy.deepcopy(messages),copy.deepcopy(tools)
        original=await self.measure(model,prepared,schemas,output,stream,**generation)
        if atomic and not original['fits']:
            raise ContextLengthError('完整证据/任务请求超过预算；未删除前缀证据或发送模型请求')
        report=dict(original)
        dropped,compacted=0,0
        # Optional protocol exchanges are removed whole. Never clip the current query or tools.
        while True:
            if report['fits']:
                report.update(original_input_bytes=original['input_bytes'],original_input_tokens=original['input_tokens'],
                              dropped_messages=dropped,compacted_frames=compacted)
                return PreparedReader(prepared,schemas,model,output,report)
            current=next((i for i in range(len(prepared)-1,-1,-1) if prepared[i].get('role')=='user'),None)
            frame=1 if len(prepared)>2 and prepared[0].get('role')=='system' and ContextBudget._is_frame(prepared[1]) else None
            protected={i for i,m in enumerate(prepared) if m.get('role')=='system'}|{i for i in (current,frame) if i is not None}
            optional=[u for u in ContextBudget._protocol_units(prepared) if not any(i in protected for i in u)]
            if optional:
                remove=set(optional[0]);dropped+=len(remove)
                prepared=[m for i,m in enumerate(prepared) if i not in remove]
                report=await self.measure(model,prepared,schemas,output,stream,**generation)
                continue
            if frame is not None:
                content=prepared[frame]['content']; minimum=minimum_context_frame_chars(content)
                if len(content)>minimum:
                    target=max(minimum,len(content)//2)
                    revised=compact_context_frame(content,target)
                    if revised!=content:
                        prepared[frame]={**prepared[frame],'content':revised};compacted+=1
                        report=await self.measure(model,prepared,schemas,output,stream,**generation)
                        continue
            raise ContextLengthError('系统提示、完整当前输入、工具定义或输出预留超过reader预算；未截断查询或发送请求')
