"""Feishu event normalization; shared durable state lives in state.py."""
import json
from .state import ChannelState as FeishuState, digest

def parse_event(payload: dict, app_id: str) -> dict | None:
    header=payload.get('header',{})
    event=payload.get('event',{})
    sender,event_message=event.get('sender',{}),event.get('message',{})
    if header.get('app_id')!=app_id or header.get('event_type')!='im.message.receive_v1':return None
    if sender.get('sender_type')!='user' or event_message.get('chat_type')!='p2p':return None
    open_id=sender.get('sender_id',{}).get('open_id');tenant=header.get('tenant_key')
    message_id,chat_id=event_message.get('message_id'),event_message.get('chat_id')
    if not all(isinstance(value,str) and 0<len(value)<=256 for value in (open_id,tenant,message_id,chat_id)):return None
    text=''
    kind=event_message.get('message_type')
    if kind=='text':
        try:text=json.loads(event_message.get('content','{}')).get('text','')
        except (ValueError,TypeError):return None
        if not isinstance(text,str) or not text.strip():return None
        if len(text)>20000:kind='oversize';text=''
    pairing=None
    pieces=text.strip().split()
    if len(pieces)==2 and pieces[0].lower() in {'/bind','/绑定'}:
        pairing=digest(pieces[1].upper());text='/bind'
    try:created=float(event_message.get('create_time',0))/1000
    except (ValueError,TypeError):return None
    return dict(message_id=message_id,chat_id=chat_id,sender=digest(tenant+':'+open_id),
                kind=kind,text=text,pair_hash=pairing,created=created)
