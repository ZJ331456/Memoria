"""Rebuildable, account-local original-experience index; never approved state.

This first stage is lexical BM25 over versioned source spans. It neither calls a
model nor assigns event dates, speaker identities or semantic support labels.
"""
from __future__ import annotations

import hashlib
from collections import Counter

from ..store import normalize_time, now
from .lexical import token_sequence

CHARS, OVERLAP = 640, 96


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


def retrieve(claims, query, *, limit=32, recorded_as_of=None):
    store = claims.store
    with store._memory_transaction():
        anchors = candidates(store, query, limit=min(limit,16), recorded_as_of=recorded_as_of)
        context = neighbors(store, anchors[:4], recorded_as_of=recorded_as_of, limit=min(8,max(0,limit-len(anchors))))
        result = []
        for rank, row in enumerate(anchors+context,1):
            source = store.message_source(row["message_id"])
            evidence = {"message_id":row["message_id"], "start":row["start_char"], "end":row["end_char"],
                        "content_sha256":row["content_sha256"]}
            # Same source checks as claims; no approval or semantic support implied.
            claims._evidence({"evidence":[evidence]})
            quote = source["content"][evidence["start"]:evidence["end"]]
            result.append({"id":"experience:"+row["id"], "kind":"source", "score":.8/(60+rank),
                "covers":[], "source_ids":[row["message_id"]], "data":{
                    "memory_id":None,"source_type":"experience", "session_id":row["session_id"],
                    "speaker_role":row["role"], "observed_at":row["observed_at"], "event_time":None,
                    "text":quote, "evidence":{**evidence,"quote":quote},
                    "source_dependency":store.message_dependency(source),
                    "retrieval":{"lane":"experience_bm25", "bm25_score":row["score"],
                                 "stage":"anchor" if row in anchors else "adjacent_chunk"},
                    "approved_personal_state":False,"semantic_support_verified":False}})
        missing = store.db.execute("SELECT COUNT(*) FROM messages m WHERE "+personal_source_sql()+
            " AND NOT EXISTS(SELECT 1 FROM experience_versions v WHERE v.message_id=m.id "
            "AND v.content_sha256=memoria_sha256(m.content)) AND m.created_at<=?",
            (normalize_time(recorded_as_of or now()),)).fetchone()[0]
        return result, {"lane":"experience_bm25; not dense/RRF", "anchors":len(anchors),
            "expanded_chunks":len(context),"candidate_limit":limit,"backlog_sources":missing,
            "recorded_as_of":recorded_as_of,"event_time_inferred":False,"model_calls":0}
