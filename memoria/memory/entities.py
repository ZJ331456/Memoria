"""Tenant-local, source-bound entity identities. No vector-based identity merging."""
from __future__ import annotations
import json,re,sqlite3,unicodedata,uuid
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from ..claims import canonical,sha
from ..store import now
from .library import LibraryError

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)

class Anchor(Strict):
    domain: Literal['conversation','library']
    source_id: str=Field(min_length=1,max_length=64)
    revision_id: str|None=Field(default=None,max_length=64)
    start: int=Field(ge=0)
    end: int=Field(gt=0)
    quote: str=Field(min_length=1,max_length=4000)
    sha256: str=Field(pattern=r'^[a-f0-9]{64}$')

class EntityInput(Strict):
    canonical_name: str=Field(min_length=2,max_length=120)
    entity_type: Literal['person','project','organization','place','concept']
    scope: Literal['personal','working']='personal'
    session_id: str|None=Field(default=None,max_length=64)
    evidence: Anchor

class AliasInput(Strict):
    expected_revision: int=Field(ge=1)
    name: str=Field(min_length=2,max_length=120)
    scope: Literal['personal','working']='personal'
    session_id: str|None=Field(default=None,max_length=64)
    evidence: Anchor

class EntityDecision(Strict):
    expected_revision: int=Field(ge=1)


def key(text):return unicodedata.normalize('NFKC',text).strip().casefold()

def spans(text,name):
    # Maintain original character offsets even for full-width and case-fold expansions.
    folded=[];offset=[]
    for i,c in enumerate(text):
        value=unicodedata.normalize('NFKC',c).casefold();folded.append(value);offset.extend([i]*len(value))
    view=''.join(folded);needle=key(name);result=[]
    if not needle:return result
    for match in re.finditer(re.escape(needle),view):
        a,b=match.span()
        if needle[0].isascii() and needle[0].isalnum() and a and re.match(r'[a-z0-9_]',view[a-1]):continue
        if needle[-1].isascii() and needle[-1].isalnum() and b<len(view) and re.match(r'[a-z0-9_]',view[b]):continue
        start,end=offset[a],offset[b-1]+1
        if key(text[start:end])==needle:result.append((start,end))
    return result

class EntityCatalog:
    def __init__(self,store,library):
        self.store,self.library=store,library
        schema="""
        CREATE TABLE IF NOT EXISTS memory_entities(id TEXT PRIMARY KEY,canonical_name TEXT NOT NULL,
            entity_type TEXT NOT NULL,scope TEXT NOT NULL,session_id TEXT,anchor_json TEXT NOT NULL,
            revision INTEGER NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_entity_aliases(id TEXT PRIMARY KEY,entity_id TEXT NOT NULL REFERENCES memory_entities(id),
            name TEXT NOT NULL,name_key TEXT NOT NULL,scope TEXT NOT NULL,session_id TEXT,
            anchor_json TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_entity_alias ON memory_entity_aliases(name_key,status);
        CREATE TABLE IF NOT EXISTS memory_entity_mentions(id TEXT PRIMARY KEY,domain TEXT NOT NULL,target_id TEXT NOT NULL,
            target_revision TEXT NOT NULL,entity_id TEXT NOT NULL REFERENCES memory_entities(id),alias_id TEXT NOT NULL,
            mention_role TEXT NOT NULL,anchor_json TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_entity_target ON memory_entity_mentions(domain,target_id,target_revision);
        CREATE INDEX IF NOT EXISTS idx_entity_mentions ON memory_entity_mentions(entity_id,domain);
        CREATE TABLE IF NOT EXISTS memory_entity_cohorts(domain TEXT NOT NULL,target_id TEXT NOT NULL,
            target_revision TEXT NOT NULL,identity_json TEXT NOT NULL,PRIMARY KEY(domain,target_id,target_revision));
        CREATE TABLE IF NOT EXISTS memory_entity_events(id INTEGER PRIMARY KEY,entity_id TEXT NOT NULL,
            action TEXT NOT NULL,revision INTEGER NOT NULL,details_json TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS entity_mention_immutable BEFORE UPDATE ON memory_entity_mentions
            BEGIN SELECT RAISE(ABORT,'entity mentions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS entity_event_immutable BEFORE UPDATE ON memory_entity_events
            BEGIN SELECT RAISE(ABORT,'entity events are immutable'); END;
        """
        with store._memory_transaction():
            statement=''
            for line in schema.splitlines(keepends=True):
                statement+=line
                if sqlite3.complete_statement(statement):store.db.execute(statement);statement=''

    def _scope(self,scope,session_id):
        if (scope=='working') != bool(session_id):raise LibraryError(422,'工作实体/别名必须绑定会话，个人范围不能带会话')
        if session_id and not self.store.session(session_id):raise LibraryError(404,'会话不存在')

    def source(self,anchor):
        anchor=Anchor.model_validate(anchor) if isinstance(anchor,dict) else anchor
        if anchor.domain=='conversation':
            from .experience import personal_source_sql
            row=self.store.db.execute('SELECT * FROM messages m WHERE id=? AND '+personal_source_sql('m'),(anchor.source_id,)).fetchone()
            if not row or anchor.revision_id:raise LibraryError(409,'实体证据须是仍有效的本人原话')
            source=dict(row);scope='personal';session=source['session_id']
        else:
            source=self.library._source(anchor.source_id)
            if source['head_id']!=anchor.revision_id:raise LibraryError(409,'实体证据资料版本已变化')
            scope,session=source['scope'],source['session_id'];source=self.library._revision(source['head_id'])
        text=source['content']
        if sha(text)!=anchor.sha256 or not 0<=anchor.start<anchor.end<=len(text) or text[anchor.start:anchor.end]!=anchor.quote:
            raise LibraryError(409,'实体证据SHA/原文位置不一致')
        return text,scope,session

    def _anchor_scope(self,anchor,scope,session_id):
        _,source_scope,source_session=self.source(anchor)
        if source_scope=='working' and (scope!='working' or session_id!=source_session):
            raise LibraryError(422,'工作资料不能升级为账号全局实体或其他会话别名')

    def _entity(self,eid):
        row=self.store.db.execute('SELECT * FROM memory_entities WHERE id=?',(eid,)).fetchone()
        if not row:raise LibraryError(404,'实体不存在')
        return dict(row)

    def live(self,entity,alias=None,*,session_id=None):
        try:
            if entity['status']!='active' or entity['scope']=='working' and entity['session_id']!=session_id:return False
            self.source(json.loads(entity['anchor_json']))
            if alias:
                if alias['status']!='active' or alias['scope']=='working' and alias['session_id']!=session_id:return False
                self.source(json.loads(alias['anchor_json']))
            return True
        except LibraryError:return False

    def _event(self,eid,action,revision,details):
        self.store.db.execute('INSERT INTO memory_entity_events(entity_id,action,revision,details_json,created_at) VALUES(?,?,?,?,?)',
            (eid,action,revision,canonical(details),now()))

    def create(self,body):
        with self.store._memory_transaction():
            self._scope(body.scope,body.session_id);self._anchor_scope(body.evidence,body.scope,body.session_id)
            if key(body.evidence.quote)!=key(body.canonical_name):raise LibraryError(422,'规范名必须绑定同名原样片段')
            if self.store.db.execute('SELECT count(*) FROM memory_entities').fetchone()[0]>=256:
                raise LibraryError(422,'首版实体目录上限256，请先整理')
            eid=uuid.uuid4().hex;anchor=canonical(body.evidence.model_dump())
            self.store.db.execute("INSERT INTO memory_entities VALUES(?,?,?,?,?,?,1,'active',?)",
                (eid,body.canonical_name,body.entity_type,body.scope,body.session_id,anchor,now()))
            self.store.db.execute("INSERT INTO memory_entity_aliases VALUES(?,?,?,?,?,?,?,'active',?)",
                (uuid.uuid4().hex,eid,body.canonical_name,key(body.canonical_name),body.scope,body.session_id,anchor,now()))
            self._event(eid,'create',1,{'basis':'explicit_source_bound_declaration'})
            return self.get(eid,session_id=body.session_id)

    def alias(self,eid,body):
        with self.store._memory_transaction():
            entity=self._entity(eid)
            if entity['revision']!=body.expected_revision:raise LibraryError(409,'实体版本已变化')
            if not self.live(entity,session_id=body.session_id):raise LibraryError(409,'实体已失效或不适用')
            self._scope(body.scope,body.session_id);self._anchor_scope(body.evidence,body.scope,body.session_id)
            if entity['scope']=='working' and (body.scope!='working' or body.session_id!=entity['session_id']):raise LibraryError(422,'不能扩大实体范围')
            if re.search(r'我的|我们|这个|该项目|this |my |our ',body.name,re.I) and body.scope!='working':raise LibraryError(422,'相对指代别名必须限定会话')
            # Finite explicit alias grammar; no model confidence or vector identity vote.
            a,b=map(re.escape,(key(entity['canonical_name']),key(body.name)))
            quote=key(body.evidence.quote)
            if not re.fullmatch(
                rf'(?:{a}\s*(?:又名|也叫|简称|别名为|也称为|也称|叫作|即)\s*[“\"‘]?{b}|{b}\s*(?:又名|也叫|简称|别名为|也称为|也称|叫作|即)\s*[“\"‘]?{a})[”\"’。.!]*',quote):
                raise LibraryError(422,'别名缺少明确肯定的同一实体原文绑定')
            if len(list(self.store.db.execute('SELECT id FROM memory_entity_aliases WHERE entity_id=?',(eid,))))>=16:raise LibraryError(422,'每实体最多16个别名')
            self.store.db.execute("INSERT INTO memory_entity_aliases VALUES(?,?,?,?,?,?,?,'active',?)",
                (uuid.uuid4().hex,eid,body.name,key(body.name),body.scope,body.session_id,canonical(body.evidence.model_dump()),now()))
            self.store.db.execute('UPDATE memory_entities SET revision=revision+1 WHERE id=?',(eid,))
            self._event(eid,'alias_add',body.expected_revision+1,{'name':body.name,'basis':'explicit_alias_clause'})
            return self.get(eid,session_id=body.session_id)

    def revoke(self,eid,expected):
        with self.store._memory_transaction():
            entity=self._entity(eid)
            if entity['revision']!=expected:raise LibraryError(409,'实体版本已变化')
            self.store.db.execute("UPDATE memory_entities SET status='retired',revision=revision+1 WHERE id=?",(eid,))
            self._event(eid,'revoke',expected+1,{})
            return {'id':eid,'status':'retired','revision':expected+1}

    def get(self,eid,*,session_id=None):
        with self.store.lock:
            entity=self._entity(eid)
            if entity['scope']=='working' and entity['session_id']!=session_id:raise LibraryError(404,'实体不属于此会话')
            aliases=[dict(a) for a in self.store.db.execute('SELECT * FROM memory_entity_aliases WHERE entity_id=?',(eid,))]
            return {k:entity[k] for k in ('id','canonical_name','entity_type','scope','session_id','revision','status')}|{
                'available':self.live(entity,session_id=session_id),
                'aliases':[{'id':a['id'],'name':a['name'],'scope':a['scope']} for a in aliases if self.live(entity,a,session_id=session_id)]}

    def match(self,text,*,session_id=None,entity_type=None):
        with self.store.lock:
            groups={}
            rows=self.store.db.execute("SELECT a.* FROM memory_entity_aliases a JOIN memory_entities e ON e.id=a.entity_id WHERE a.status='active' AND e.status='active' ORDER BY a.id").fetchall()
            for row in rows:
                alias=dict(row);entity=self._entity(alias['entity_id'])
                if entity_type and entity_type!=entity['entity_type']:continue
                if not self.live(entity,alias,session_id=session_id):continue
                found=spans(text,alias['name'])
                if found:groups.setdefault(alias['name_key'],[]).append((entity,alias,found))
            matches=[];ambiguous=[]
            for name,items in groups.items():
                ids={e['id'] for e,_,_ in items}
                if len(ids)>1:ambiguous.append({'name':name,'entity_ids':sorted(ids)});continue
                e,a,found=items[0]
                matches.extend({'entity_id':e['id'],'entity_type':e['entity_type'],'alias_id':a['id'],'start':x,'end':y,
                    'basis':'unique_exact_source_alias'} for x,y in found)
            return {'matches':matches,'ambiguous':ambiguous}

    def bind(self,domain,target_id,target_revision,anchor,*,session_id=None,role='context'):
        with self.store._memory_transaction():
            self.source(anchor);result=self.match(anchor.quote,session_id=session_id)
            for hit in result['matches']:
                span=anchor.model_copy(update={'start':anchor.start+hit['start'],'end':anchor.start+hit['end'],
                    'quote':anchor.quote[hit['start']:hit['end']]})
                fields=[domain,target_id,target_revision,hit['entity_id'],hit['alias_id'],span.model_dump()]
                self.store.db.execute('INSERT OR IGNORE INTO memory_entity_mentions VALUES(?,?,?,?,?,?,?,?,?)',
                    (sha(canonical(fields)),domain,target_id,target_revision,hit['entity_id'],hit['alias_id'],role,canonical(span.model_dump()),now()))
            previous=self.cohort(domain,target_id,target_revision)
            identity=sorted(set(previous+[h['entity_id'] for h in result['matches']]))
            if result['ambiguous']:identity.append('ambiguous:'+sha(canonical(anchor.model_dump())))
            self.store.db.execute('INSERT OR REPLACE INTO memory_entity_cohorts VALUES(?,?,?,?)',
                (domain,target_id,target_revision,canonical(sorted(set(identity)))))
            return result

    def cohort(self,domain,target_id,target_revision):
        row=self.store.db.execute('SELECT identity_json FROM memory_entity_cohorts WHERE domain=? AND target_id=? AND target_revision=?',
            (domain,target_id,target_revision)).fetchone()
        return json.loads(row[0]) if row else []

    def signature(self,domain,target_id,target_revision,*,session_id=None):
        ids=set()
        for row in self.store.db.execute('SELECT * FROM memory_entity_mentions WHERE domain=? AND target_id=? AND target_revision=?',(domain,target_id,target_revision)):
            entity=self._entity(row['entity_id']);alias=self.store.db.execute('SELECT * FROM memory_entity_aliases WHERE id=?',(row['alias_id'],)).fetchone()
            try:self.source(json.loads(row['anchor_json']))
            except LibraryError:continue
            if alias and self.live(entity,dict(alias),session_id=session_id):
                resolution=self.match(json.loads(row['anchor_json'])['quote'],session_id=session_id)
                if not resolution['ambiguous'] and entity['id'] in {h['entity_id'] for h in resolution['matches']}:ids.add(entity['id'])
        return sorted(ids)
