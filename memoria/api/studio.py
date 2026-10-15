"""Read-only, tenant-bound console projections; never a model or memory write."""
from __future__ import annotations
import json
import re
from typing import Literal
from fastapi import APIRouter, HTTPException, Query, Request


def literal_pattern(value: str) -> str:
    return '%' + value.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'


def readable_text(value: str) -> str:
    # Presentation/search projection only. Preserve English spacing, digits and
    # line breaks; source content, hashes and Claim spans always remain original.
    return re.sub(r'(?<=[\u3400-\u9fff])[ \t]+(?=[\u3400-\u9fff，。！？、；：])|(?<=[，。！？、；：])[ \t]+(?=[\u3400-\u9fff])','',value)


def excerpt(text: str, query: str = '', limit: int = 160) -> str:
    text,query=readable_text(text),readable_text(query)
    at=text.casefold().find(query.casefold()) if query else 0
    start=max(0,at-35)
    return ('…' if start else '') + text[start:start+limit] + ('…' if len(text)>start+limit else '')


def studio_router(store, claims, service=None):
    router=APIRouter(prefix='/api/studio',tags=['studio'])
    if store is not None:store.db.create_function('studio_readable',1,readable_text,deterministic=True)
    def ready():
        if store is None:raise HTTPException(503,'Console is unavailable')

    def origins():
        result={}
        for row in store.db.execute('SELECT dataset_id,receipt_json FROM dataset_imports'):
            receipt=json.loads(row['receipt_json'])
            for item in receipt.get('source_mapping',[]):
                if isinstance(item,dict) and isinstance(item.get('session_id'),str):
                    result[item['session_id']]={'kind':'imported','dataset_id':row['dataset_id'],'source_id':item.get('source_id')}
        return result

    def browse(q,origin,sort,page,page_size):
        sources=origins();clauses=[];params=[]
        if q:
            clauses.append("(studio_readable(s.title) LIKE ? ESCAPE '\\' OR EXISTS(SELECT 1 FROM messages m WHERE m.session_id=s.id AND studio_readable(m.content) LIKE ? ESCAPE '\\'))")
            params.extend([literal_pattern(readable_text(q))]*2)
        if origin!='all':
            clauses.append('s.id '+('IN' if origin=='imported' else 'NOT IN')+' (SELECT value FROM json_each(?))')
            params.append(json.dumps(list(sources)))
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        total=store.db.execute('SELECT COUNT(*) FROM sessions s'+where,params).fetchone()[0]
        pages=max(1,(total+page_size-1)//page_size);page=min(page,pages)
        order={'recent':'s.updated_at DESC,s.rowid ASC','oldest':'s.created_at ASC,s.rowid ASC','messages':'message_count DESC,s.rowid ASC'}[sort]
        rows=store.db.execute('''SELECT s.id,s.title,s.created_at,s.updated_at,
            (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) AS message_count,
            (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id AND studio_readable(m.content) LIKE ? ESCAPE '\\') AS matching_messages,
            COALESCE((SELECT m.id FROM messages m WHERE m.session_id=s.id AND studio_readable(m.content) LIKE ? ESCAPE '\\' ORDER BY m.rowid LIMIT 1),
                (SELECT m.id FROM messages m WHERE m.session_id=s.id ORDER BY m.rowid LIMIT 1)) AS preview_message_id
            FROM sessions s'''+where+' ORDER BY '+order+' LIMIT ? OFFSET ?',
            [literal_pattern(readable_text(q)),literal_pattern(readable_text(q)),*params,page_size,(page-1)*page_size]).fetchall()
        items=[]
        for row in rows:
            item=dict(row);text=store.db.execute('SELECT content FROM messages WHERE id=?',(item['preview_message_id'],)).fetchone()
            item.update(preview=excerpt(text[0],q) if text else '',origin=sources.get(item['id'],{'kind':'native'}))
            if not q:item['matching_messages']=0
            items.append(item)
        return {'items':items,'total':total,'page':page,'page_size':page_size,'pages':pages,'query':q}

    @router.get('/summary')
    def summary():
        ready()
        with store.lock:
            counts=store.overview()
            current=store.db.execute('''SELECT r.status,COUNT(*) n FROM claim_roots c JOIN claim_revisions r ON r.claim_id=c.id
                WHERE c.user_id=? AND r.revision=(SELECT MAX(r2.revision) FROM claim_revisions r2 WHERE r2.claim_id=c.id)
                GROUP BY r.status''',(claims.user_id,)).fetchall()
            grouped={row['status']:row['n'] for row in current}
            reviews=store.db.execute("SELECT COUNT(*) FROM memory_reviews WHERE status='pending'").fetchone()[0]
            return {**counts,'claims':sum(grouped.values()),'claim_statuses':grouped,'pending_reviews':reviews,
                    'datasets':store.db.execute('SELECT COUNT(*) FROM dataset_imports').fetchone()[0],
                    'imported_conversations':store.db.execute('SELECT COUNT(*) FROM sessions WHERE id IN (SELECT value FROM json_each(?))',
                        (json.dumps(list(origins())),)).fetchone()[0],
                    'recent':browse('','all','recent',1,5)['items'],
                    'scope':'current_authenticated_user','model_called':False}

    @router.get('/conversations')
    def conversations(q:str=Query(default='',max_length=200),origin:Literal['all','imported','native']='all',
                      sort:Literal['recent','oldest','messages']='recent',page:int=Query(default=1,ge=1),
                      page_size:int=Query(default=20,ge=1,le=100)):
        ready()
        with store.lock:return browse(q.strip(),origin,sort,page,page_size)

    @router.get('/pipeline')
    def pipeline(request: Request):
        """Explain persisted stages without invoking an extractor or opening secrets."""
        ready()
        from ..memory.experience import personal_source_sql
        with store.lock:
            counts = store.overview()
            imported = {r['id'] for r in store.db.execute('SELECT id FROM sessions WHERE id IN (SELECT value FROM json_each(?))',
                                                       (json.dumps(list(origins())),))}
            eligible = store.db.execute('SELECT COUNT(*) FROM messages m WHERE '+personal_source_sql('m')).fetchone()[0]
            job_counts = {r['status']:r['n'] for r in store.db.execute('SELECT status,COUNT(*) n FROM memory_jobs GROUP BY status')}
            review_counts = {r['status']:r['n'] for r in store.db.execute('SELECT status,COUNT(*) n FROM memory_reviews GROUP BY status')}
            recent = [dict(r) for r in store.db.execute('''SELECT j.id,j.source_ref,j.status,j.attempts,j.created_at,j.updated_at,
                j.extracted_count,j.staged_count,m.session_id,
                (SELECT COUNT(*) FROM memory_reviews r WHERE r.job_id=j.id) review_count,
                CASE WHEN j.error IS NULL THEN 0 ELSE 1 END has_error
                FROM memory_jobs j LEFT JOIN messages m ON m.id=j.source_ref ORDER BY j.created_at DESC LIMIT 50''')]
            legacy = store.db.execute("SELECT COUNT(*) FROM memory_jobs WHERE status='completed' AND extracted_count IS NULL").fetchone()[0]
            known_empty = store.db.execute("SELECT COUNT(*) FROM memory_jobs WHERE status='completed' AND extracted_count=0").fetchone()[0]
            claim_statuses = {r['status']:r['n'] for r in store.db.execute('''SELECT r.status,COUNT(*) n FROM claim_roots c JOIN claim_revisions r
                ON r.claim_id=c.id WHERE c.user_id=? AND r.revision=(SELECT MAX(x.revision) FROM claim_revisions x WHERE x.claim_id=c.id) GROUP BY r.status''', (claims.user_id,))}
            namespace = store.embedding_namespace
            indexes = {'memory_vectors':store.db.execute('SELECT COUNT(*) FROM memory_embeddings WHERE namespace=?',(namespace,)).fetchone()[0],
                       'memory_chunks':store.db.execute('SELECT COUNT(*) FROM memory_embedding_chunks WHERE namespace=?',(namespace,)).fetchone()[0],
                       'experience_chunks':store.db.execute('SELECT COUNT(*) FROM experience_chunks c JOIN messages m ON m.id=c.message_id WHERE '+personal_source_sql('m')).fetchone()[0],
                       'experience_vectors':store.db.execute('SELECT COUNT(*) FROM experience_vectors WHERE namespace=?',(namespace,)).fetchone()[0]}
        task = getattr(request.app.state,'memory_worker_task',None)
        worker_state = 'unavailable' if task is None else 'stopped' if task.cancelled() else 'failed' if task.done() and task.exception() else 'stopped' if task.done() else 'running'
        reasons = []
        if imported: reasons.append({'code':'public_history','text':'导入的公开资料保留为实验来源，不自动变成本人的长期事实。'})
        if legacy: reasons.append({'code':'legacy_outcome_unknown','text':'部分旧任务只有完成状态，没有详细抽取计数；未见候选不能直接判定模型返回了空数组。'})
        if known_empty: reasons.append({'code':'empty_extraction','text':'已有任务明确返回0条长期记忆候选；普通提问不一定包含值得长期保存的事实。'})
        if not review_counts.get('pending',0): reasons.append({'code':'no_pending_review','text':'当前没有待审候选。可从自己的有效原话提出候选，再核对后批准。'})
        if counts['memories']==0: reasons.append({'code':'no_active_memory','text':'尚无有效长期记忆。对话保存、候选提取和审核入库是不同步骤。'})
        models, integrations = {}, []
        if service is not None:
            for role, config in [('main',service.settings.main),('fast',service.settings.fast),('embedding',service.settings.embedding)]:
                models[role]={'model':config.model,'ready':config.is_ready(role='embedding' if role=='embedding' else 'llm')}
            integrations=[{'name':s['name'],'enabled':s['enabled'],'connected':s['connected'],'tool_count':len(s['tools']),
                           'has_error':bool(s['error'])} for s in service.mcp.status()['servers']]
        return {'counts':{**counts,'imported_conversations':len(imported),'native_conversations':counts['sessions']-len(imported),
                          'eligible_personal_messages':eligible,'reviews':review_counts,'claims':claim_statuses},
                'extraction':{'statuses':job_counts,'known_empty':known_empty,'legacy_outcome_unknown':legacy,'recent':recent,
                              'worker':worker_state,'recent_limit':50},'indexes':indexes,'models':models,'integrations':integrations,
                'integration_startup':service.integration_status() if service is not None else {'state':'unknown'},
                'reasons':reasons,'scope':'current_authenticated_user','model_called':False}

    @router.get('/conversations/{session_id}/messages')
    def messages(session_id:str,page:int=Query(default=1,ge=1),page_size:int=Query(default=50,ge=1,le=100),
                 q:str=Query(default='',max_length=200),anchor_id:str=Query(default='',max_length=128)):
        ready()
        with store.lock:
            session=store.session(session_id)
            if not session:raise HTTPException(404,'会话不存在')
            total=store.db.execute('SELECT COUNT(*) FROM messages WHERE session_id=?',(session_id,)).fetchone()[0]
            pages=max(1,(total+page_size-1)//page_size)
            if anchor_id:
                anchor=store.db.execute('SELECT rowid FROM messages WHERE id=? AND session_id=?',(anchor_id,session_id)).fetchone()
                if not anchor:raise HTTPException(404,'消息不属于当前会话')
                ordinal=store.db.execute('SELECT COUNT(*) FROM messages WHERE session_id=? AND rowid<=?',(session_id,anchor[0])).fetchone()[0]
                page=(ordinal-1)//page_size+1
            page=min(page,pages);offset=(page-1)*page_size
            rows=store.db.execute('SELECT id,session_id,role,content,created_at FROM messages WHERE session_id=? ORDER BY rowid LIMIT ? OFFSET ?',
                                  (session_id,page_size,offset)).fetchall()
            q=q.strip();matches=[];matched_total=0
            if q:
                pattern=literal_pattern(readable_text(q))
                matched_total=store.db.execute("SELECT COUNT(*) FROM messages WHERE session_id=? AND studio_readable(content) LIKE ? ESCAPE '\\'",(session_id,pattern)).fetchone()[0]
                matched=store.db.execute('''WITH ranked AS(SELECT id,content,ROW_NUMBER() OVER(ORDER BY rowid) ordinal FROM messages WHERE session_id=?)
                    SELECT id,ordinal FROM ranked WHERE studio_readable(content) LIKE ? ESCAPE '\\' ORDER BY ordinal LIMIT 200''',(session_id,pattern)).fetchall()
                matches=[{'id':row['id'],'ordinal':row['ordinal'],'page':(row['ordinal']-1)//page_size+1} for row in matched]
            return {'session':{**session,'message_count':total,'origin':origins().get(session_id,{'kind':'native'})},
                    'items':[{**dict(row),'display_content':readable_text(row['content']),'ordinal':offset+i+1} for i,row in enumerate(rows)],
                    'total':total,'page':page,'page_size':page_size,'pages':pages,'matches':matches,
                    'matched_total':matched_total,'matches_truncated':matched_total>len(matches),
                    'time_basis':'recorded_at; imported messages do not establish event dates'}
    @router.get('/memories')
    def memories(q:str=Query(default='',max_length=200),status:Literal['active','superseded','all']='active',
                 page:int=Query(default=1,ge=1),page_size:int=Query(default=50,ge=1,le=100)):
        ready()
        clauses=[];params=[]
        if q.strip():clauses.append("content LIKE ? ESCAPE '\\'");params.append(literal_pattern(q.strip()))
        if status!='all':clauses.append('status=?');params.append(status)
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        with store.lock:
            total=store.db.execute('SELECT COUNT(*) FROM memories'+where,params).fetchone()[0]
            page=min(page,max(1,(total+page_size-1)//page_size))
            rows=store.db.execute('SELECT id,content,kind,importance,source,source_ref,status,created_at,updated_at,reinforcement FROM memories'+where+
                ' ORDER BY updated_at DESC,rowid DESC LIMIT ? OFFSET ?',[*params,page_size,(page-1)*page_size]).fetchall()
            return {'items':[dict(row) for row in rows],'total':total,'page':page,'page_size':page_size,'model_called':False}
    return router
