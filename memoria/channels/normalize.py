"""Normalize private-text commands before any source text reaches the inbox."""
from .state import digest


def packet(*,message_id,chat_id,sender,text,created,kind='text',**extra):
    if not isinstance(text,str):return None
    text=text.strip()
    if not text and kind=='text':return None
    if len(text)>20000:kind='oversize';text=''
    pieces=text.split()
    pairing=None
    if len(pieces)==2 and pieces[0].lower() in {'/bind','/绑定','/start'}:
        pairing=digest(pieces[1].upper());text='/bind'
    return dict(message_id=message_id,chat_id=chat_id,sender=digest(sender),text=text,
                kind=kind,created=created,pair_hash=pairing,**extra)
