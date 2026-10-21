"""Bounded proposal extraction and literal coverage diagnostics, never admission."""
from __future__ import annotations
import json
import math
import re
import uuid
from ..store import now
from .source_support import digest

MAX_ITEMS=12


def clauses(text):
    return [(m.start(),m.end()) for m in re.finditer(r'[^，,。！？!?；;\n]+',text) if m.group().strip()]


def allowance(text):
    return min(MAX_ITEMS,max(4,len(clauses(text)),math.ceil(len(text)/300)))


def output_tokens(base,quota):return min(4096,base+280*max(0,quota-4))


def request_topic(item):
    """A literal request object can group subtasks; never normalizes asserted facts."""
    if item.category!='interest' or item.asserted or item.durable:return item.topic
    found=re.search(r'(?:查找|搜索|查阅|查询)([^，。！？?；;\n]{1,80}?)的'
        r'(?:入门|维护|性能分析|分析|使用|实现|安装|部署|优化|介绍|学习)(?:资料|文章|教程|文档|指南)?[。.!]*$',item.quote)
    return found[1].strip().casefold() if found else item.topic


def envelope(value,field,maximum=32):
    if not isinstance(value,dict) or set(value)!={field} or not isinstance(value[field],list) or len(value[field])>maximum:
        raise ValueError('Invalid bounded extraction envelope')
    return value[field]


def proposals(value,model,text,validate,*,quota=None):
    from pydantic import ValidationError
    rows=envelope(value,'observations');accepted=[];rejected=[];seen=set()
    quota=quota or allowance(text)
    for index,row in enumerate(rows):
        try:
            item=model.model_validate(row)
            validate(item)
            identity=json.dumps(item.model_dump(),ensure_ascii=False,sort_keys=True)
            if identity in seen:raise ValueError('duplicate_candidate')
            if len(accepted)>=quota:raise ValueError('candidate_quota_exceeded')
            seen.add(identity);accepted.append((index,item))
        except (ValueError,TypeError) as exc:
            # Original output stays private; diagnostics never expose invalid raw objects.
            known={'duplicate_candidate','candidate_quota_exceeded','source_quote_not_unique',
                'credential_like_observation','empty_normalized_observation','feedback_quote_missing',
                'feedback_quote_unexpected','source_quote_not_literal','history_id_not_supplied_or_duplicate',
                'history_subject_not_supplied','history_subject_not_linked','history_relation_without_target','history_condition_mismatch'}
            reason='schema_invalid' if isinstance(exc,ValidationError) else str(exc) if str(exc) in known else 'source_constraint'
            rejected.append({'index':index,'reason':reason,'error_type':type(exc).__name__})
    return accepted,{'quota':quota,'proposed':len(rows),'accepted':len(accepted),
        'accepted_indices':[i for i,_ in accepted],'rejected':rejected}


def coverage(text,quotes,*,offset=0):
    intervals=[]
    for quote in quotes:
        if quote and text.count(quote)==1:
            start=text.index(quote);intervals.append((start,start+len(quote)))
    uncovered=[];all_clauses=clauses(text)
    for a,b in all_clauses[:96]:
        cursor=a
        for start,end in sorted(intervals):
            if start>cursor:break
            if end>cursor:cursor=end
        if cursor<b:uncovered.append({'start':a+offset,'end':b+offset,'quote':text[a:b],
            'reason':'not_fully_covered_by_valid_observation_quote'})
    return {'kind':'literal_clause_coverage_not_semantic_recall','semantic_completeness_verified':False,
        'clause_count':len(all_clauses),'uncovered':uncovered,'truncated':len(all_clauses)>96,
        'status':'needs_inspection' if uncovered or len(all_clauses)>96 else 'literal_quotes_cover_source'}


def install(store):
    store.db.execute('''CREATE TABLE IF NOT EXISTS memory_extraction_diagnostics(
        id TEXT PRIMARY KEY,domain TEXT NOT NULL,source_id TEXT NOT NULL,revision_id TEXT NOT NULL,
        source_sha TEXT NOT NULL,policy TEXT NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,
        diagnostics_json TEXT NOT NULL,raw_json TEXT,created_at TEXT NOT NULL)''')
    store.db.execute('CREATE INDEX IF NOT EXISTS idx_extraction_source ON memory_extraction_diagnostics(domain,source_id,policy)')
    store.db.execute('''CREATE TRIGGER IF NOT EXISTS extraction_source_deleted AFTER DELETE ON messages
        BEGIN DELETE FROM memory_extraction_diagnostics WHERE domain='conversation' AND source_id=OLD.id; END''')
    if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='library_sources'").fetchone():
        store.db.execute('''CREATE TRIGGER IF NOT EXISTS extraction_library_deleted AFTER DELETE ON library_sources
            BEGIN DELETE FROM memory_extraction_diagnostics WHERE domain='library' AND source_id=OLD.id; END''')


def record(store,domain,source_id,revision,source_sha,policy,start,end,diagnostic,raw):
    with store._memory_transaction():
        store.db.execute('INSERT INTO memory_extraction_diagnostics VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (uuid.uuid4().hex,domain,source_id,revision,source_sha,policy,start,end,
             json.dumps(diagnostic,ensure_ascii=False),json.dumps(raw,ensure_ascii=False),now()))


def latest(store,domain,source_id,policy):
    row=store.db.execute('''SELECT diagnostics_json FROM memory_extraction_diagnostics
        WHERE domain=? AND source_id=? AND policy=? ORDER BY rowid DESC LIMIT 1''',(domain,source_id,policy)).fetchone()
    return json.loads(row[0]) if row else None


def visible(store,*,session_id=None,limit=20):
    from .formation_store import eligible_source
    result=[]
    for row in store.db.execute('SELECT * FROM memory_extraction_diagnostics ORDER BY rowid DESC LIMIT ?', (limit*4,)):
        try:
            if row['domain']=='conversation':
                source=eligible_source(store,row['source_id'])
                if session_id and source['session_id']!=session_id:continue
                if digest(source['content'])!=row['source_sha']:continue
            else:
                catalog=getattr(store,'entity_catalog',None)
                if not catalog:continue
                source=catalog.library._source(row['source_id'])
                if source['head_id']!=row['revision_id'] or source['scope']=='working' and source['session_id']!=session_id:continue
                if session_id and source['session_id'] not in {None,session_id}:continue
            result.append({k:row[k] for k in ('id','domain','source_id','revision_id','source_sha','policy','start','end','created_at')}|
                {'diagnostics':json.loads(row['diagnostics_json'])})
            if len(result)==limit:break
        except (ValueError,LookupError):continue
        except Exception as exc:
            from .library import LibraryError
            if not isinstance(exc,LibraryError):raise
    return result
