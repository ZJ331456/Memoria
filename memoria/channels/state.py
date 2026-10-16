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


class ChannelState:
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
            CREATE TABLE IF NOT EXISTS checkpoints(app TEXT PRIMARY KEY,offset INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS delivery_parts(app TEXT,message_id TEXT,part INTEGER,status TEXT,
                PRIMARY KEY(app,message_id,part));
        ''')

    def close(self):self.db.close()

    def cursor(self,app):
        with self.lock:row=self.db.execute('SELECT offset FROM checkpoints WHERE app=?',(app,)).fetchone()
        return row[0] if row else 0

    def advance(self,app,offset):
        with self.lock,self.db:
            self.db.execute('INSERT INTO checkpoints VALUES(?,?) ON CONFLICT(app) DO UPDATE SET offset=MAX(offset,excluded.offset)',(app,offset))

    def delivery_status(self,app,message_id,part):
        with self.lock:row=self.db.execute('SELECT status FROM delivery_parts WHERE app=? AND message_id=? AND part=?',(app,message_id,part)).fetchone()
        return row[0] if row else None

    def delivery_part(self,app,message_id,part,status):
        with self.lock,self.db:
            self.db.execute('INSERT OR REPLACE INTO delivery_parts VALUES(?,?,?,?)',(app,message_id,part,status))

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
                return '账号已有其他绑定，请先在原账号或对应平台中解绑'
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
            self.db.execute('DELETE FROM delivery_parts WHERE app=? AND NOT EXISTS (SELECT 1 FROM inbox WHERE inbox.app=delivery_parts.app AND inbox.message_id=delivery_parts.message_id)',(app,))
