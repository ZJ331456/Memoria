"""Server-owned subject/provenance and three-clock semantics for native sources.

No model date/identity becomes authoritative. Legacy rows remain unknown; schema
installation neither reprocesses private history nor backdates messages.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..store import now, normalize_time
from .source_support import digest
from .temporal_resolver import EventTime, TemporalResolver, ZONE

POLICY='observation-semantics-v1'


def aware_time(value):
    if value is None:return None
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None:raise ValueError('Source time must include its original timezone')
    return dt.isoformat()  # Preserve the original calendar timezone for relative dates.


class Semantics(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    policy:str=POLICY
    subject_ref:str
    source_role:Literal['user','assistant','system','tool','external','task_report','self_document','unknown_document']
    epistemic_status:Literal['plan','hypothesis','question','reported','source_assertion','explicit_statement','unconfirmed']
    event_status:Literal['planned','hypothetical','question','negated','asserted','unknown']
    event_time:EventTime|None=None
    time_precision:str='unknown'
    time_reason:str
    source_time:str|None=None
    source_time_basis:Literal['live_received','platform_event','declared_original','unknown','legacy_unknown']
    source_recorded_at:str
    recorded_at:str
    condition_scope:dict
    evidence_ref:list[dict]=Field(max_length=8)
    _aware=field_validator('source_time','source_recorded_at','recorded_at')(aware_time)


def install(store):
    schema='''
    CREATE TABLE IF NOT EXISTS memory_source_clocks(
        domain TEXT NOT NULL,revision_ref TEXT NOT NULL,source_id TEXT NOT NULL,
        source_sha TEXT NOT NULL,source_role TEXT NOT NULL,source_time TEXT,
        basis TEXT NOT NULL,recorded_at TEXT NOT NULL,PRIMARY KEY(domain,revision_ref));
    CREATE TABLE IF NOT EXISTS memory_observation_semantics(
        domain TEXT NOT NULL,observation_id TEXT NOT NULL,data_json TEXT NOT NULL,
        PRIMARY KEY(domain,observation_id));
    CREATE TRIGGER IF NOT EXISTS source_clock_immutable BEFORE UPDATE ON memory_source_clocks
    BEGIN SELECT RAISE(ABORT,'source clocks are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS observation_semantics_immutable BEFORE UPDATE ON memory_observation_semantics
    BEGIN SELECT RAISE(ABORT,'observation semantics are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS source_clock_message_deleted AFTER DELETE ON messages
    BEGIN DELETE FROM memory_source_clocks WHERE domain='conversation' AND revision_ref=OLD.id; END;
    CREATE TRIGGER IF NOT EXISTS source_clock_conversation_protected BEFORE DELETE ON memory_source_clocks
    WHEN OLD.domain='conversation' AND EXISTS(SELECT 1 FROM messages WHERE id=OLD.revision_ref)
    BEGIN SELECT RAISE(ABORT,'cannot replace a live source clock'); END;
    '''
    statement=''
    for line in schema.splitlines(keepends=True):
        statement+=line
        if sqlite3.complete_statement(statement):store.db.execute(statement);statement=''
    for table,domain in [('memory_observations','conversation'),('library_observations','library')]:
        if store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
            store.db.execute(f"CREATE TRIGGER IF NOT EXISTS semantics_{domain}_deleted AFTER DELETE ON {table} BEGIN DELETE FROM memory_observation_semantics WHERE domain='{domain}' AND observation_id=OLD.id; END")
            store.db.execute(f"CREATE TRIGGER IF NOT EXISTS semantics_{domain}_protected BEFORE DELETE ON memory_observation_semantics WHEN OLD.domain='{domain}' AND EXISTS(SELECT 1 FROM {table} WHERE id=OLD.observation_id) BEGIN SELECT RAISE(ABORT,'cannot replace live observation semantics'); END")
    store.db.execute('CREATE INDEX IF NOT EXISTS idx_observation_source_semantics ON memory_observations(message_id,state)')
    if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='library_revisions'").fetchone():
        store.db.execute("CREATE TRIGGER IF NOT EXISTS source_clock_library_deleted AFTER DELETE ON library_revisions BEGIN DELETE FROM memory_source_clocks WHERE domain='library' AND revision_ref=OLD.id; END")
        store.db.execute("CREATE TRIGGER IF NOT EXISTS source_clock_library_protected BEFORE DELETE ON memory_source_clocks WHEN OLD.domain='library' AND EXISTS(SELECT 1 FROM library_revisions WHERE id=OLD.revision_ref) BEGIN SELECT RAISE(ABORT,'cannot replace a live source clock'); END")
        store.db.execute('CREATE INDEX IF NOT EXISTS idx_library_observation_semantics ON library_observations(revision_id)')


def record_clock(store,domain,source_id,revision_ref,content,role,recorded_at,source_time=None,*,live=False,basis_override=None):
    source_time=aware_time(source_time)
    basis='declared_original' if source_time else 'live_received' if live else 'unknown'
    if source_time and basis_override in {'platform_event','declared_original'}:basis=basis_override
    if live and source_time is None:source_time=datetime.fromisoformat(recorded_at).astimezone(ZONE).isoformat()
    store.db.execute('INSERT INTO memory_source_clocks VALUES(?,?,?,?,?,?,?,?)',(
        domain,revision_ref,source_id,digest(content),role,source_time,basis,recorded_at))


def clock(store,domain,source_id,revision_ref,content,role,recorded_at):
    row=store.db.execute('SELECT * FROM memory_source_clocks WHERE domain=? AND revision_ref=?',(domain,revision_ref)).fetchone()
    if row and (row['source_id'],row['source_sha'],row['source_role'])==(source_id,digest(content),role):return dict(row)
    return {'domain':domain,'revision_ref':revision_ref,'source_id':source_id,'source_sha':digest(content),
        'source_role':role,'source_time':None,'basis':'legacy_unknown','recorded_at':recorded_at}


def message_clock(store,source):
    mid=source.get('id',source.get('message_id'))
    return clock(store,'conversation',mid,mid,source['content'],source['role'],source['created_at'])


def library_role(source):
    return 'task_report' if source['kind']=='task_result' else 'external' if source['authorship']=='external' else 'self_document' if source['authorship']=='self' else 'unknown_document'


def library_clock(library,source,revision):
    return clock(library.store,'library',source['id'],revision['id'],revision['content'],library_role(source),revision['created_at'])


THIRD=re.compile(r'朋友|同事|父亲|母亲|伴侣|室友|孩子|老板|同学|他说|她说|他们|\b(?:he|she|they|my friend)\b',re.I)
SELF=re.compile(r'我(?!们)|本人|\b(?:I|my|me)\b',re.I)
PLAN=re.compile(r'计划|打算|准备|想要|希望|预计|\b(?:plan(?:ning)? to|intend to|will)\b',re.I)
HYPOTHESIS=re.compile(r'假如|假设|倘若|要是|\b(?:suppose|hypothetically)\b',re.I)
EVENT_NEGATION=re.compile(r'(?:未|没有|没能|尚未|并未|从未)(?:能|曾)?(?:完成|交付|发布|归档|启动|迁移|实现|解决)|没有(?:任何)?计划|不打算|不准备|\b(?:did not|didn\x27t|never completed)\b',re.I)


def build(store,domain,source,revision,item,*,recorded_at=None,group=None,history_audit=None,quote_start=None,subject_binding=None):
    """Current source proves the event. Historical associations only disambiguate."""
    text=revision['content'];quote=item['quote'];start=text.index(quote) if quote_start is None else quote_start
    if (quote_start is None and text.count(quote)!=1) or start<0 or text[start:start+len(quote)]!=quote:
        raise ValueError('Semantic evidence must uniquely bind the current source or validated selected span')
    c=message_clock(store,source) if domain=='conversation' else clock(store,domain,source['id'],revision['id'],text,library_role(source),revision['created_at'])
    evidence=[{'domain':domain,'source_id':source['id'],'revision_ref':c['revision_ref'],
        'source_sha256':digest(text),'start':start,'end':start+len(quote),'quote':quote,'purpose':'fact'}]
    if subject_binding:
        a,b=subject_binding['start'],subject_binding['end']
        if text[a:b]!=subject_binding['quote']:raise ValueError('Subject evidence changed')
        evidence.append({**evidence[0],'start':a,'end':b,'quote':text[a:b],'purpose':'subject'})
    temporal_quote=item.get('temporal_quote','') or quote
    if temporal_quote!=quote:
        a=start-temporal_quote.index(quote) if temporal_quote.count(quote)==1 else -1
        if a<0 or text[a:a+len(temporal_quote)]!=temporal_quote or len(temporal_quote)>3000:
            raise ValueError('Temporal context must uniquely contain the current fact quote')
        before,_,after=temporal_quote.partition(quote)
        if re.search(r'[。；;！？!?\n]',before+after):raise ValueError('Temporal context crosses a statement boundary')
        evidence.append({**evidence[0],'start':a,'end':a+len(temporal_quote),'quote':temporal_quote,'purpose':'event_time'})
    reference=datetime.fromisoformat(c['source_time']) if c['source_time'] else None
    resolved=TemporalResolver().resolve_event(temporal_quote,reference);event=resolved['event_time']
    status=('hypothetical' if HYPOTHESIS.search(temporal_quote) else
        'question' if re.search(r'[？?]|为什么|怎么办',temporal_quote) or item.get('epistemic')=='question' else
        'negated' if EVENT_NEGATION.search(temporal_quote) else
        'planned' if PLAN.search(temporal_quote) or item.get('epistemic')=='plan' or item.get('category')=='goal' else
        'negated' if item.get('polarity')=='negative' else
        'asserted' if item.get('asserted',item.get('epistemic') in {'asserted','observed'}) else 'unknown')
    epistemic=('plan' if status=='planned' else 'hypothesis' if status=='hypothetical' else 'question' if status=='question' else
        'reported' if c['source_role']=='task_report' else 'source_assertion' if domain=='library' else
        'explicit_statement' if item.get('asserted') else 'unconfirmed')
    request_actor=(domain=='conversation' and c['source_role']=='user' and item.get('category') in {'interest','behavior'}
        and not item.get('asserted') and not THIRD.search(quote))  # Actor of THIS interaction, not a confirmed preference.
    subject='self' if request_actor else 'unknown';catalog=getattr(store,'entity_catalog',None)
    if catalog:
        lookup=catalog.match(quote,session_id=source.get('session_id'))
        entity_ids={m['entity_id'] for m in lookup['matches']} if not lookup['ambiguous'] else set()
        named=(group or {}).get('subject','')
        if subject=='unknown' and named.startswith('entity:') and named[7:] in entity_ids:subject=named
        elif subject=='unknown' and len(entity_ids)==1 and not SELF.search(quote):subject='entity:'+next(iter(entity_ids))
    if subject=='unknown':
        if c['source_role'] in {'user','self_document'} and SELF.search(quote) and not THIRD.search(quote):subject='self'
        elif domain=='library' and (group or {}).get('subject') not in {None,'unknown','未知'}:
            subject='source:'+source['id']+':'+digest(group['subject'])[:16]  # Same free name != same entity.
        elif history_audit and history_audit.get('status')=='verified' and item.get('subject_ref','unknown')!='unknown':subject=item['subject_ref']
    from .condition_parser import ConditionParser
    clauses=[cl for cl in ConditionParser().clauses(text) if cl.start<start+len(quote) and start<cl.end]
    scopes={json.dumps(cl.scope,sort_keys=True) for cl in clauses}
    condition={'label':item.get('scope',item.get('conditions','')),'constraints':json.loads(next(iter(scopes))) if len(scopes)==1 else {},
        'status':'unknown' if len(scopes)!=1 or any(cl.ambiguous for cl in clauses) else 'scoped' if any(cl.scope for cl in clauses) else 'unconditional'}
    if condition['status']=='unconditional' and condition['label'].strip().lower() not in {'','默认','无','无条件','default','none','unconditional'}:
        condition['status']='unknown'  # A free-text scope isn't proof that no condition exists.
    for cl in clauses:
        if cl.binding and len(evidence)<8:
            a,b=cl.binding
            if not any(e['purpose']=='condition' and e['start']==a and e['end']==b for e in evidence):
                evidence.append({**evidence[0],'start':a,'end':b,'quote':text[a:b],'purpose':'condition'})
    return Semantics(subject_ref=subject,source_role=c['source_role'],epistemic_status=epistemic,event_status=status,
        event_time=event,time_precision=event.precision if event else 'unknown',time_reason=resolved['reason'],
        source_time=c['source_time'],source_time_basis=c['basis'],source_recorded_at=c['recorded_at'],
        recorded_at=recorded_at or now(),condition_scope=condition,evidence_ref=evidence).model_dump(mode='json')


def save(store,domain,oid,data):
    store.db.execute('INSERT OR IGNORE INTO memory_observation_semantics VALUES(?,?,?)',(domain,oid,json.dumps(data,ensure_ascii=False)))


def load(store,domain,oid):
    row=store.db.execute('SELECT data_json FROM memory_observation_semantics WHERE domain=? AND observation_id=?',(domain,oid)).fetchone()
    return json.loads(row[0]) if row else None


def projection(data):
    """Budgeted Reader metadata; full SHA/quotes remain in the immutable receipt/API."""
    return {k:data[k] for k in ('subject_ref','source_role','epistemic_status','event_status','event_time','time_precision',
        'source_time','source_time_basis','source_recorded_at','recorded_at','condition_scope','time_reason')}|{
        'source_ref':{k:data['evidence_ref'][0][k] for k in ('domain','source_id','revision_ref','source_sha256')},
        'evidence_ref':[{k:e[k] for k in ('start','end','purpose')} for e in data['evidence_ref']]}


def public_clock(value):
    return {k:value[k] for k in ('source_time','basis','recorded_at','source_role')}


def source_observations(store,domain,source_id,*,start=0,end=None,library=None):
    """Only still-live observations wholly contained in the displayed raw quote."""
    if domain=='conversation':
        from .formation_store import live_observation
        rows=store.db.execute("SELECT * FROM memory_observations WHERE message_id=? AND state IN('active','archived') ORDER BY rowid DESC LIMIT 32",(source_id,)).fetchall()
        result=[]
        for row in rows:
            try:live_observation(store,row,allow_archived=True)
            except ValueError:continue
            if row['start']<start or (end is not None and row['end']>end):continue
            data=load(store,domain,row['id'])
            if data and all(e['start']>=start and (end is None or e['end']<=end) for e in data['evidence_ref']):result.append({'observation_id':row['id'],**projection(data)})
        return result
    rows=store.db.execute('''SELECT o.id,c.start,c.end FROM library_observations o JOIN library_cards c ON c.id=o.card_id
        JOIN library_sources s ON s.id=o.source_id WHERE o.revision_id=? AND s.status='active' AND s.head_id=o.revision_id
        AND c.status='approved' ORDER BY o.rowid DESC LIMIT 32''',(source_id,)).fetchall()
    result=[]
    for r in rows:
        if not library or not library.formation._live(library.formation._observation(r['id']),complete=False):continue
        if r['start']<start or (end is not None and r['end']>end):continue
        data=load(store,domain,r['id'])
        if data and all(e['start']>=start and (end is None or e['end']<=end) for e in data['evidence_ref']):result.append({'observation_id':r['id'],**projection(data)})
    return result


def matches(data,*,event_start=None,event_end=None,recorded_as_of=None,subject_ref=None):
    if subject_ref and data['subject_ref']!=subject_ref:return False
    if recorded_as_of and normalize_time(data['recorded_at'])>normalize_time(recorded_as_of):return False
    if event_start or event_end:
        t=data.get('event_time')
        if not t:return False
        # A year-sized uncertainty interval merely overlapping March is NOT
        # evidence that the event happened in March. Require full containment.
        if event_start and normalize_time(t['lower'])<normalize_time(event_start):return False
        if event_end and normalize_time(t['upper'])>normalize_time(event_end):return False
    return True
