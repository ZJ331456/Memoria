"""Private durable inbox, one-time pairing and sender-to-account ownership."""
from __future__ import annotations
import hashlib
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


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


class FeishuState:
    def __init__(self,path:Path):
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.db=sqlite3.connect(path,check_same_thread=False,timeout=0.5)
        self.db.row_factory=sqlite3.Row;self.lock=threading.RLock()
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS pairing(app TEXT,owner TEXT,hash TEXT UNIQUE,expires REAL,PRIMARY KEY(app,owner));
            CREATE TABLE IF NOT EXISTS bindings(app TEXT,sender TEXT,owner TEXT,session TEXT,created REAL,
                PRIMARY KEY(app,sender),UNIQUE(app,owner));
            CREATE TABLE IF NOT EXISTS inbox(app TEXT,message_id TEXT,sender TEXT,body TEXT,owner TEXT,session TEXT,
                status TEXT,reply TEXT,error TEXT,created REAL,attempts INTEGER NOT NULL DEFAULT 0,next_try REAL NOT NULL DEFAULT 0,PRIMARY KEY(app,message_id));
            CREATE TABLE IF NOT EXISTS attempts(app TEXT,sender TEXT,at REAL);
        ''')

    def close(self):self.db.close()

    def issue(self,app:str,owner:str)->dict:
        code=secrets.token_hex(6).upper();expiry=time.time()+600
        with self.lock,self.db:
            self.db.execute('INSERT OR REPLACE INTO pairing VALUES(?,?,?,?)',(app,owner,digest(code),expiry))
        return {'code':code,'expires_at':expiry,'command':'/绑定 '+code}

    def bind(self,app:str,sender:str,code_hash:str)->str:
        now=time.time()
        with self.lock,self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('DELETE FROM attempts WHERE at<?',(now-600,))
            count=self.db.execute('SELECT COUNT(*) FROM attempts WHERE app=? AND sender=?',(app,sender)).fetchone()[0]
            if count>=5:raise ValueError('尝试过于频繁，请十分钟后再试')
            self.db.execute('INSERT INTO attempts VALUES(?,?,?)',(app,sender,now))
            code=self.db.execute('SELECT owner FROM pairing WHERE app=? AND hash=? AND expires>?',(app,code_hash,now)).fetchone()
            if not code:return '绑定码无效或已过期，请在Chat重新生成'
            existing=self.db.execute('SELECT owner FROM bindings WHERE app=? AND sender=?',(app,sender)).fetchone()
            other=self.db.execute('SELECT sender FROM bindings WHERE app=? AND owner=?',(app,code['owner'])).fetchone()
            if (existing and existing['owner']!=code['owner']) or (other and other['sender']!=sender):
                return '账号已有其他绑定，请先在原账号或飞书中解绑'
            self.db.execute('INSERT OR IGNORE INTO bindings VALUES(?,?,?,?,?)',(app,sender,code['owner'],None,now))
            self.db.execute('DELETE FROM pairing WHERE app=? AND owner=?',(app,code['owner']))
            return '绑定成功。现在可以直接发文字与Memoria对话；记录归入你自己的账号。发送 /new 开始新对话，/stop 停止，/unbind 解绑。'

    def binding(self,app:str,sender:str):
        with self.lock:
            row=self.db.execute('SELECT * FROM bindings WHERE app=? AND sender=?',(app,sender)).fetchone()
            return dict(row) if row else None

    def bound(self,app:str,owner:str)->bool:
        with self.lock:return bool(self.db.execute('SELECT 1 FROM bindings WHERE app=? AND owner=?',(app,owner)).fetchone())

    def unbind(self,app:str,owner:str):
        with self.lock,self.db:
            self.db.execute('DELETE FROM bindings WHERE app=? AND owner=?',(app,owner))
            self.db.execute('DELETE FROM pairing WHERE app=? AND owner=?',(app,owner))

    def session(self,app:str,sender:str,owner:str,session:str|None):
        with self.lock,self.db:self.db.execute('UPDATE bindings SET session=? WHERE app=? AND sender=? AND owner=?',(session,app,sender,owner))

    def ingest(self,app:str,packet:dict)->bool:
        with self.lock,self.db:
            self.db.execute('BEGIN IMMEDIATE')
            binding=self.db.execute('SELECT owner,created FROM bindings WHERE app=? AND sender=?',(app,packet['sender'])).fetchone()
            durable={**packet,'binding_created':binding['created'] if binding else None}
            result=self.db.execute('INSERT OR IGNORE INTO inbox(app,message_id,sender,body,owner,status,created) VALUES(?,?,?,?,?,?,?)',
                (app,packet['message_id'],packet['sender'],json.dumps(durable,ensure_ascii=False),binding['owner'] if binding else None,'received',time.time()))
            return result.rowcount==1

    def pending(self,app:str,limit=64):
        with self.lock:return [dict(row) for row in self.db.execute(
            "SELECT * FROM inbox WHERE app=? AND status IN ('received','generated','delivery_failed') AND next_try<=? ORDER BY CASE WHEN lower(trim(json_extract(body,'$.text'))) IN ('/stop','/停止') THEN 0 ELSE 1 END,rowid LIMIT ?",(app,time.time(),limit))]

    def cancel_pending(self,app:str,sender:str,except_message:str):
        with self.lock,self.db:
            self.db.execute("UPDATE inbox SET status='ignored',body='{}',reply=NULL,error='queue_cancelled' WHERE app=? AND sender=? AND message_id!=? AND status='received'",(app,sender,except_message))

    def allowed_turn(self,app:str,sender:str)->bool:
        now=time.time();key='turn:'+sender
        with self.lock,self.db:
            self.db.execute('DELETE FROM attempts WHERE at<?',(now-600,))
            count=self.db.execute('SELECT COUNT(*) FROM attempts WHERE app=? AND sender=?',(app,key)).fetchone()[0]
            if count>=30:return False
            self.db.execute('INSERT INTO attempts VALUES(?,?,?)',(app,key,now));return True

    def delivery_failed(self,app:str,message_id:str,code:str):
        with self.lock,self.db:
            self.db.execute("UPDATE inbox SET status='delivery_failed',attempts=attempts+1,next_try=?,error=? WHERE app=? AND message_id=?",(time.time()+30,code,app,message_id))
            self.db.execute("UPDATE inbox SET status='failed',body='{}',reply=NULL WHERE app=? AND message_id=? AND attempts>=3",(app,message_id))

    def claim(self,app:str,message_id:str)->bool:
        with self.lock,self.db:
            return self.db.execute("UPDATE inbox SET status='processing' WHERE app=? AND message_id=? AND status='received'",(app,message_id)).rowcount==1

    def update(self,app:str,message_id:str,status:str,*,reply:str|None=None,owner=None,session=None,error=None):
        with self.lock,self.db:
            self.db.execute('UPDATE inbox SET status=?,reply=COALESCE(?,reply),owner=COALESCE(?,owner),session=COALESCE(?,session),error=? WHERE app=? AND message_id=?',
                (status,reply,owner,session,error,app,message_id))
            if status in {'sent','ignored','interrupted','failed'}:
                self.db.execute("UPDATE inbox SET body='{}',reply=NULL WHERE app=? AND message_id=?",(app,message_id))

    def recover(self,app:str):
        with self.lock,self.db:
            self.db.execute("UPDATE inbox SET status='interrupted',body='{}',reply=NULL,error='restart_interrupted' WHERE app=? AND status='processing'",(app,))
            self.db.execute("DELETE FROM inbox WHERE created<? AND status IN ('sent','ignored','interrupted','failed')",(time.time()-7*86400,))
            self.db.execute('DELETE FROM pairing WHERE expires<?',(time.time(),))
