"""Rebuildable, account-local original-experience index; never approved state.

This first stage is lexical BM25 over versioned source spans. It neither calls a
model nor assigns event dates, speaker identities or semantic support labels.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter

from ..store import normalize_time, now
from .lexical import token_sequence

CHARS, OVERLAP = 640, 96


def local_encoder(embedder):
    if not embedder or not getattr(embedder,"enabled",False):return False
    if getattr(embedder,"is_local",False):return True
    # The admin-configured Memoria loopback BGE service is also supported.
    # Loopback is a deployment assumption, not remote-model identity proof.
    from .embedding import EmbeddingClient
    return (isinstance(embedder,EmbeddingClient) and embedder.config.is_local_http()
            and embedder.config.model.replace('\\','/').split('/')[-1] in {'bge-small-zh-v1.5','bge-small-en-v1.5'})


def bounded_encoder(embedder):
    from .embedding import EmbeddingClient
    if isinstance(embedder,EmbeddingClient) and not embedder.is_local:
        return EmbeddingClient(embedder.config,timeout_seconds=min(8,embedder.timeout_seconds),max_retries=0)
    return embedder


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def install(store):
    store.db.executescript("""
        CREATE TABLE IF NOT EXISTS experience_versions (
            message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
            content_sha256 TEXT NOT NULL, indexed_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS experience_chunks (
            id TEXT PRIMARY KEY, message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            part INTEGER NOT NULL, start_char INTEGER NOT NULL, end_char INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL, token_count INTEGER NOT NULL, title TEXT NOT NULL,
            UNIQUE(message_id,part));
        CREATE INDEX IF NOT EXISTS idx_experience_message ON experience_chunks(message_id,part);
        CREATE TABLE IF NOT EXISTS experience_terms (
            chunk_id TEXT NOT NULL REFERENCES experience_chunks(id) ON DELETE CASCADE,
            token TEXT NOT NULL, tf INTEGER NOT NULL, PRIMARY KEY(chunk_id,token));
        CREATE INDEX IF NOT EXISTS idx_experience_token ON experience_terms(token,chunk_id);
        CREATE TRIGGER IF NOT EXISTS experience_source_changed
            BEFORE UPDATE OF content,role,session_id,created_at ON messages
            WHEN NEW.content!=OLD.content OR NEW.role!=OLD.role OR NEW.session_id!=OLD.session_id
                OR NEW.created_at!=OLD.created_at BEGIN
            DELETE FROM experience_chunks WHERE message_id=OLD.id;
            DELETE FROM experience_versions WHERE message_id=OLD.id;
        END;
    """)
    store.db.execute("""CREATE TABLE IF NOT EXISTS experience_vectors (
        chunk_id TEXT NOT NULL REFERENCES experience_chunks(id) ON DELETE CASCADE,
        namespace TEXT NOT NULL, dimension INTEGER NOT NULL, embedding TEXT NOT NULL,
        content_sha256 TEXT NOT NULL, PRIMARY KEY(chunk_id,namespace,dimension))""")
    reindex(store, 32)


def personal_source_sql(alias="m"):
    # Filter before corpus statistics / ranking / LIMIT, not after candidate loss.
    return (f"{alias}.role='user' AND NOT EXISTS (SELECT 1 FROM memory_source_revocations r WHERE r.message_id={alias}.id) "
        "AND NOT EXISTS (SELECT 1 FROM dataset_imports d, "
        "json_each(d.receipt_json,'$.source_mapping') g, json_each(g.value,'$.message_ids') p "
        f"WHERE p.value={alias}.id)")


def index_message(store, message):
    mid, text = message["id"], message["content"]
    store.db.execute("DELETE FROM experience_chunks WHERE message_id=?", (mid,))
    store.db.execute("DELETE FROM experience_versions WHERE message_id=?", (mid,))
    if message["role"] != "user":
        return
    fingerprint, start, part = digest(text), 0, 0
    while start < len(text):
        end = min(len(text), start+CHARS)
        chunk = text[start:end]
        cid = digest(f"{mid}:{fingerprint}:{start}:{end}")
        terms = Counter(token_sequence(chunk))
        store.db.execute("INSERT INTO experience_chunks VALUES(?,?,?,?,?,?,?,?)",
            (cid, mid, part, start, end, fingerprint, sum(terms.values()), chunk[:96]))
        store.db.executemany("INSERT INTO experience_terms VALUES(?,?,?)",
            [(cid, token, tf) for token, tf in terms.items()])
        if end == len(text):
            break
        start, part = end-OVERLAP, part+1
    store.db.execute("INSERT INTO experience_versions VALUES(?,?,?)", (mid, fingerprint, now()))


def reindex(store, limit=16):
    if type(limit) is not int or not 1 <= limit <= 64:
        raise ValueError("experience index batch must be 1..64")
    with store._memory_transaction():
        missing = (personal_source_sql()+" AND NOT EXISTS (SELECT 1 FROM experience_versions v "
            "WHERE v.message_id=m.id AND v.content_sha256=memoria_sha256(m.content))")
        count, oldest = store.db.execute("SELECT COUNT(*),MIN(m.created_at) FROM messages m WHERE "+missing).fetchone()
        rows = store.db.execute("SELECT m.* FROM messages m WHERE "+missing+" ORDER BY m.created_at,m.id LIMIT ?", (limit,)).fetchall()
        for row in rows:
            index_message(store, dict(row))
        return {"indexed_sources":len(rows), "remaining_sources":count-len(rows),
                "oldest_missing_before_batch":oldest, "batch_limit":limit, "model_calls":0}


def _eligible(cutoff):
    sql = personal_source_sql()+" AND c.content_sha256=memoria_sha256(m.content)"
    sql += " AND m.created_at<=?"
    params = [normalize_time(cutoff or now())]
    return sql, params


def candidates(store, query, *, limit=16, recorded_as_of=None):
    terms = sorted(set(token_sequence(query)))[:64]
    if not terms:
        return []
    if type(limit) is not int or not 1 <= limit <= 64:
        raise ValueError("experience candidate limit must be 1..64")
    scope, params = _eligible(recorded_as_of)
    marks = ",".join("?" for _ in terms)
    with store._memory_transaction():
        rows = store.db.execute(f"""WITH eligible AS (
            SELECT c.* FROM experience_chunks c JOIN messages m ON m.id=c.message_id WHERE {scope}
        ), corpus AS (SELECT COUNT(*) n,COALESCE(AVG(token_count),1) average FROM eligible),
        df AS (SELECT t.token,COUNT(*) frequency FROM experience_terms t JOIN eligible e ON e.id=t.chunk_id
               WHERE t.token IN ({marks}) GROUP BY t.token),
        ranked AS (SELECT t.chunk_id,SUM(memoria_bm25(t.tf,e.token_count,c.average,c.n,df.frequency)) score
                   FROM experience_terms t JOIN eligible e ON e.id=t.chunk_id JOIN df ON df.token=t.token CROSS JOIN corpus c
                   GROUP BY t.chunk_id)
        SELECT e.*,r.score,m.session_id,m.role,m.created_at observed_at FROM ranked r
            JOIN eligible e ON e.id=r.chunk_id JOIN messages m ON m.id=e.message_id
            ORDER BY r.score DESC,m.created_at DESC,e.id LIMIT ?""", [*params,*terms,limit]).fetchall()
        return [dict(row) for row in rows]


def neighbors(store, anchors, *, recorded_as_of=None, limit=8):
    """Adjacent chunks of an anchor's same source; no unbounded history scan."""
    if type(limit) is not int or not 0 <= limit <= 16:
        raise ValueError("experience neighbor limit must be 0..16")
    scope, params = _eligible(recorded_as_of)
    result, seen = [], {a["id"] for a in anchors}
    with store._memory_transaction():
        for anchor in anchors[:4]:
            if len(result) >= limit:
                break
            rows = store.db.execute("SELECT c.*,m.session_id,m.role,m.created_at observed_at FROM experience_chunks c "
                "JOIN messages m ON m.id=c.message_id WHERE "+scope+
                " AND c.message_id=? AND c.content_sha256=? AND c.part IN (?,?) ORDER BY c.part LIMIT ?",
                [*params, anchor["message_id"],anchor["content_sha256"],anchor["part"]-1,anchor["part"]+1,limit-len(result)]).fetchall()
            for row in rows:
                if row["id"] not in seen:
                    result.append({**dict(row), "score":anchor["score"]*.5})
                    seen.add(row["id"])
    return result


def _materialize(claims, anchors, *, limit=32, recorded_as_of=None, lane="experience_bm25"):
    store = claims.store
    with store._memory_transaction():
        context = neighbors(store, anchors[:4], recorded_as_of=recorded_as_of, limit=min(8,max(0,limit-len(anchors))))
        result = []
        for rank, row in enumerate(anchors+context,1):
            source = store.message_source(row["message_id"])
            evidence = {"message_id":row["message_id"], "start":row["start_char"], "end":row["end_char"],
                        "content_sha256":row["content_sha256"]}
            # Index/embedding awaits cannot publish a changed source snapshot.
            from ..claims import ClaimError
            if not source or source['session_id']!=row['session_id'] or source['created_at']!=row['observed_at']:
                continue
            try:
                claims._evidence({"evidence":[evidence]})
            except ClaimError:
                continue
            quote = source["content"][evidence["start"]:evidence["end"]]
            result.append({"id":"experience:"+row["id"], "kind":"source", "score":.8/(60+rank),
                "covers":[], "source_ids":[row["message_id"]], "data":{
                    "memory_id":None,"source_type":"experience", "session_id":row["session_id"],
                    "speaker_role":row["role"], "observed_at":row["observed_at"], "event_time":None,
                    "text":quote, "evidence":{**evidence,"quote":quote},
                    "source_dependency":store.message_dependency(source),
                    "retrieval":{"lane":lane, "ranking_score":row["score"], "lanes":row.get("lanes",["bm25"]),
                                 "stage":"anchor" if row in anchors else "adjacent_chunk"},
                    "approved_personal_state":False,"semantic_support_verified":False}})
        missing = store.db.execute("SELECT COUNT(*) FROM messages m WHERE "+personal_source_sql()+
            " AND NOT EXISTS(SELECT 1 FROM experience_versions v WHERE v.message_id=m.id "
            "AND v.content_sha256=memoria_sha256(m.content)) AND m.created_at<=?",
            (normalize_time(recorded_as_of or now()),)).fetchone()[0]
        return result, {"lane":lane, "anchors":len(anchors),
            "expanded_chunks":len(context),"candidate_limit":limit,"backlog_sources":missing,
            "recorded_as_of":recorded_as_of,"event_time_inferred":False,"model_calls":0}


def retrieve(claims, query, *, limit=32, recorded_as_of=None):
    with claims.store._memory_transaction():
        anchors=candidates(claims.store,query,limit=min(limit,16),recorded_as_of=recorded_as_of)
        return _materialize(claims,anchors,limit=limit,recorded_as_of=recorded_as_of)


async def reindex_dense(store,embedder,limit=16):
    """Bounded offline BGE maintenance; never enables a remote provider."""
    if type(limit) is not int or not 1<=limit<=32:raise ValueError('dense index batch must be 1..32')
    if not local_encoder(embedder):
        return {'indexed_chunks':0,'skipped':'offline local embedding required','embedding_calls':0}
    embedder=bounded_encoder(embedder)
    namespace=embedder.namespace
    scope,params=_eligible(None)
    with store._memory_transaction():
        rows=[dict(r) for r in store.db.execute('SELECT c.*,m.content,m.session_id,m.role,m.created_at FROM experience_chunks c '
            'JOIN messages m ON m.id=c.message_id WHERE '+scope+
            ' AND NOT EXISTS(SELECT 1 FROM experience_vectors v WHERE v.chunk_id=c.id AND v.namespace=?) '
            'ORDER BY m.created_at,c.part,c.id LIMIT ?',(*params,namespace,limit))]
    if not rows:return {'indexed_chunks':0,'embedding_calls':0,'namespace':namespace}
    documents=await embedder.embed_documents([r['content'][r['start_char']:r['end_char']] for r in rows])
    if len(documents)!=len(rows):raise ValueError('Incomplete experience embedding batch')
    for doc in documents:store._validate_vector(doc.vector)
    dimensions={len(doc.vector) for doc in documents}
    if len(dimensions)!=1:raise ValueError('Experience embedding batch has mixed dimensions')
    dimension=next(iter(dimensions))
    indexed=0
    with store._memory_transaction():
        if store.embedding_namespace!=namespace:return {'indexed_chunks':0,'skipped':'embedding namespace changed','embedding_calls':1}
        if store.db.execute('SELECT 1 FROM experience_vectors WHERE namespace=? AND dimension!=? LIMIT 1',(namespace,dimension)).fetchone():
            raise ValueError('Experience embedding namespace changed dimension; use a new model namespace')
        for row,doc in zip(rows,documents,strict=True):
            current=store.db.execute('SELECT c.id FROM experience_chunks c JOIN messages m ON m.id=c.message_id WHERE c.id=? AND '+
                scope+' AND m.session_id=? AND m.created_at=?', (row['id'],*params,row['session_id'],row['created_at'])).fetchone()
            if not current:continue
            store.db.execute('INSERT OR REPLACE INTO experience_vectors VALUES(?,?,?,?,?)',
                (row['id'],namespace,len(doc.vector),json.dumps(doc.vector),row['content_sha256']))
            indexed+=1
    return {'indexed_chunks':indexed,'embedding_calls':1,'namespace':namespace,'requested_chunks':len(rows),
            'interpretation':'embedding_calls counts document API batches, not internal encoder batches'}


def dense_candidates(store,vector,namespace,*,limit=16,recorded_as_of=None):
    store._validate_vector(vector)
    scope,params=_eligible(recorded_as_of)
    with store._memory_transaction():
        rows=store.db.execute('SELECT c.*,m.session_id,m.role,m.created_at observed_at,v.embedding FROM experience_vectors v '
            'JOIN experience_chunks c ON c.id=v.chunk_id JOIN messages m ON m.id=c.message_id WHERE '+scope+
            ' AND v.namespace=? AND v.dimension=? AND v.content_sha256=c.content_sha256',(*params,namespace,len(vector))).fetchall()
        missing=store.db.execute('SELECT COUNT(*) FROM experience_chunks c JOIN messages m ON m.id=c.message_id WHERE '+scope+
            ' AND NOT EXISTS(SELECT 1 FROM experience_vectors v WHERE v.chunk_id=c.id AND v.namespace=? AND v.dimension=? '
            'AND v.content_sha256=c.content_sha256)',(*params,namespace,len(vector))).fetchone()[0]
    ranked=[];invalid=0;norm=math.sqrt(sum(x*x for x in vector))
    for row in rows:
        try:
            values=json.loads(row['embedding']);store._validate_vector(values)
            if len(values)!=len(vector):raise ValueError('dimension mismatch')
            divisor=norm*math.sqrt(sum(x*x for x in values))
            score=sum(a*b for a,b in zip(vector,values,strict=True))/divisor if divisor else -1
            if score>=.45:ranked.append({k:row[k] for k in row.keys() if k!='embedding'}|{'score':score,'lanes':['dense']})
        except (ValueError,TypeError):invalid+=1
    ranked.sort(key=lambda row:(-row['score'],row['id']))
    return ranked[:limit],{'scanned_vectors':len(rows),'invalid_vectors':invalid,'backlog_chunks':missing,
                          'namespace':namespace,'dimension':len(vector),'scan':'exact eligible vectors; not ANN'}


async def query(claims,text,embedder,*,mode='bm25',limit=32,recorded_as_of=None):
    if mode=='bm25':return retrieve(claims,text,limit=limit,recorded_as_of=recorded_as_of)
    if mode not in {'dense','rrf'}:raise ValueError('invalid experience mode')
    lexical=candidates(claims.store,text,limit=min(limit,16),recorded_as_of=recorded_as_of) if mode=='rrf' else []
    dense=[];diag={'embedding_calls':0,'available':False}
    if local_encoder(embedder):
        embedder=bounded_encoder(embedder)
        from .embedding import EmbeddingError
        namespace=embedder.namespace
        diag['embedding_calls']=1
        try:
            vector=await embedder.embed_query(text)
            if namespace!=claims.store.embedding_namespace:
                diag['reason']='embedding_namespace_changed'
            elif vector:
                dense,detail=dense_candidates(claims.store,vector,namespace,limit=min(limit,16),recorded_as_of=recorded_as_of)
                diag.update(detail,available=True)
            else:diag['reason']='empty query embedding'
        except (EmbeddingError,ValueError) as exc:
            diag['reason']='query_embedding_failed';diag['error_type']=type(exc).__name__
    else:diag['reason']='offline local embedding unavailable; dense not executed'
    by_id={};scores={};lanes={}
    for name,items in [('bm25',lexical),('dense',dense)]:
        for rank,item in enumerate(items,1):
            by_id[item['id']]=item;scores[item['id']]=scores.get(item['id'],0)+1/(60+rank)
            lanes.setdefault(item['id'],[]).append(name)
    if mode=='rrf':anchors=[{**by_id[cid],'score':score,'lanes':lanes[cid]} for cid,score in sorted(scores.items(),key=lambda p:(-p[1],p[0]))[:min(limit,16)]]
    else:anchors=dense
    result,base=_materialize(claims,anchors,limit=limit,recorded_as_of=recorded_as_of,lane='experience_'+mode)
    return result,{**base,'dense':diag,'bm25_candidates':len(lexical),'dense_candidates':len(dense),
                   'model_calls':diag['embedding_calls'],'llm_calls':0,
                   'fusion':'two-lane rank RRF k=60' if mode=='rrf' else None}
