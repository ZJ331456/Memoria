"""Observation provenance and immutable pattern proposals in the existing tenant SQLite."""
from __future__ import annotations

import json
import sqlite3
from ..store import now
from .source_support import digest
from ..claims import canonical

POLICY = 'formation-v7-subject-event-clocks'


def install(store):
    from .extraction import install as install_extraction
    install_extraction(store)
    schema = '''
        CREATE TABLE IF NOT EXISTS memory_formation_runs(
            id TEXT PRIMARY KEY,job_id TEXT NOT NULL,source_sha TEXT NOT NULL,policy TEXT NOT NULL,
            model TEXT NOT NULL,usage_json TEXT,created_at TEXT NOT NULL,
            UNIQUE(job_id,source_sha,policy));
        CREATE TABLE IF NOT EXISTS memory_observations(
            id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES memory_formation_runs(id),
            message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            source_sha TEXT NOT NULL,session_id TEXT NOT NULL,observed_at TEXT NOT NULL,
            category TEXT NOT NULL,topic TEXT NOT NULL,scope TEXT NOT NULL,polarity TEXT NOT NULL,
            content TEXT NOT NULL,quote TEXT NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,
            asserted INTEGER NOT NULL,durable INTEGER NOT NULL,importance INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'active');
        CREATE INDEX IF NOT EXISTS idx_observation_topic ON memory_observations(topic,scope,polarity,state,observed_at);
        CREATE INDEX IF NOT EXISTS idx_observation_message ON memory_observations(message_id);
        CREATE TABLE IF NOT EXISTS memory_pattern_versions(
            id TEXT PRIMARY KEY,bucket TEXT NOT NULL,content TEXT NOT NULL,action TEXT NOT NULL,
            previous_id TEXT REFERENCES memory_pattern_versions(id),input_sha TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,review_id TEXT,memory_id TEXT,created_at TEXT NOT NULL,expires_at TEXT NOT NULL,
            model TEXT NOT NULL,usage_json TEXT,reason TEXT NOT NULL,observation_ids_json TEXT NOT NULL,
            support_group_id TEXT);
        CREATE INDEX IF NOT EXISTS idx_pattern_bucket ON memory_pattern_versions(bucket,status,created_at);
        CREATE INDEX IF NOT EXISTS idx_pattern_expiry ON memory_pattern_versions(status,expires_at);
        CREATE INDEX IF NOT EXISTS idx_pattern_memory ON memory_pattern_versions(memory_id,status,expires_at);
        CREATE TABLE IF NOT EXISTS memory_formation_candidates(
            review_id TEXT PRIMARY KEY REFERENCES memory_reviews(id) ON DELETE CASCADE,
            pattern_id TEXT REFERENCES memory_pattern_versions(id),observation_ids_json TEXT NOT NULL,
            target_json TEXT);
        CREATE TABLE IF NOT EXISTS memory_formation_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,pattern_id TEXT,action TEXT NOT NULL,
            reason TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_formation_calls(
            id TEXT PRIMARY KEY,stage TEXT NOT NULL,input_sha TEXT NOT NULL,model TEXT NOT NULL,
            usage_json TEXT,status TEXT NOT NULL,error_type TEXT,duration_ms INTEGER NOT NULL,created_at TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS observation_source_changed BEFORE UPDATE ON messages
        WHEN OLD.content IS NOT NEW.content OR OLD.role IS NOT NEW.role OR OLD.session_id IS NOT NEW.session_id OR OLD.created_at IS NOT NEW.created_at
        BEGIN UPDATE memory_observations SET state='invalidated' WHERE message_id=OLD.id; END;
    '''
    statement = ''
    for line in schema.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            store.db.execute(statement)
            statement = ''
    if 'support_group_id' not in {r[1] for r in store.db.execute('PRAGMA table_info(memory_pattern_versions)')}:
        store.db.execute('ALTER TABLE memory_pattern_versions ADD COLUMN support_group_id TEXT')
    from .formation_history import install as install_history
    install_history(store)
    from .observation_semantics import install as install_semantics
    install_semantics(store)


def eligible_source(store, message_id):
    from .experience import personal_source_sql
    row = store.db.execute('SELECT m.* FROM messages m WHERE m.id=? AND ' + personal_source_sql('m'),
                           (message_id,)).fetchone()
    if not row:
        raise ValueError('Observation source is unavailable or not a personal user message')
    return dict(row)


def live_observation(store, row, *, allow_archived=False):
    if not row or row['state'] not in ({'active','archived'} if allow_archived else {'active'}):
        raise ValueError('Observation is invalidated')
    source = eligible_source(store, row['message_id'])
    if (digest(source['content']), source['session_id'], source['created_at']) != (
            row['source_sha'], row['session_id'], row['observed_at']):
        raise ValueError('Observation source version changed')
    if source['content'][row['start']:row['end']] != row['quote']:
        raise ValueError('Observation span no longer matches')
    from .formation_history import load
    load(store,row['id'])
    catalog=getattr(store,'entity_catalog',None)
    if catalog and catalog.cohort('observation',row['id'],row['source_sha'])!=catalog.signature(
        'observation',row['id'],row['source_sha'],session_id=row['session_id']):
        raise ValueError('Observation entity identity changed')
    return source


def pattern_sources(store, pattern):
    sources = []
    for oid in json.loads(pattern['observation_ids_json']):
        row = store.db.execute('SELECT * FROM memory_observations WHERE id=?', (oid,)).fetchone()
        source = live_observation(store, row, allow_archived=True)
        if source['id'] not in {s['id'] for s in sources}:
            sources.append(source)
    return sources


def bucket(row):
    row=dict(row)
    fields=[row['category'], row['topic'], row['scope'], row['polarity']]
    if row.get('entity_signature'):fields.append(row['entity_signature'])
    if row.get('history_identity'):fields.append(row['history_identity'])
    if row.get('semantic_subject'):fields.extend([row['semantic_subject'],row['semantic_modality']])
    return digest(json.dumps(fields, ensure_ascii=False))


def with_entities(store,row):
    row=dict(row);catalog=getattr(store,'entity_catalog',None)
    if catalog:
        ids=catalog.cohort('observation',row['id'],row['source_sha'])
        if ids:row['entity_signature']=canonical(ids)
    from .formation_history import load
    context=load(store,row['id'],check=False)
    if context and context['identity_key']:row['history_identity']=context['identity_key']
    from .observation_semantics import load as semantic_load,projection
    data=semantic_load(store,'conversation',row['id'])
    if data:
        row['semantics']=projection(data)
        row['semantic_subject']=data['subject_ref'] if data['subject_ref']!='unknown' else 'unresolved:'+row['message_id']
        row['semantic_modality']='nonassertion' if data['event_status'] in {'unknown','question'} else data['event_status']
    return row


def stage(store, job, receipt, reviews):
    """Called inside the same transaction that stages ordinary review candidates."""
    source = eligible_source(store, job['source_ref'])
    if digest(source['content']) != receipt['source_sha']:
        raise ValueError('Source changed during observation extraction')
    policy = receipt.get('policy', POLICY)
    from . import formation_history as history
    context=receipt.get('history_context')
    if context:history.validate(store,source,context)
    rid = digest(job['id'] + receipt['source_sha'] + policy)
    store.db.execute('INSERT OR IGNORE INTO memory_formation_runs VALUES(?,?,?,?,?,?,?)',
                     (rid, job['id'], receipt['source_sha'], policy, receipt['model'],
                      json.dumps(receipt['usage']) if receipt['usage'] is not None else None, now()))
    store.db.execute('INSERT OR IGNORE INTO memory_formation_history_runs VALUES(?,?)',(rid,json.dumps(context)))
    direct = []
    for index, item in enumerate(receipt['observations']):
        quote = item['quote']
        if source['content'].count(quote) != 1:
            raise ValueError('Observation quote is not unique in source')
        start = source['content'].index(quote)
        oid = digest(rid + ':' + str(index))
        store.db.execute('''INSERT OR IGNORE INTO memory_observations VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active')''',
            (oid, rid, source['id'], receipt['source_sha'], source['session_id'], source['created_at'],
             item['category'], item['topic'], item['scope'], item['polarity'], item['content'], quote,
             start, start+len(quote), int(item['asserted']), int(item['durable']), item['importance']))
        proposal={key:item.get(key,default) for key,default in (
            ('linked_memory_ids',[]),('context_message_ids',[]),('subject_ref','unknown'),('relation','independent'))}
        audit=receipt.get('history_audits',[{'status':'not_requested'}]*len(receipt['observations']))[index]
        identity=history.identity(item,source,audit['status']=='verified',store)
        store.db.execute('INSERT OR IGNORE INTO memory_observation_history VALUES(?,?,?,?,?)',
            (oid,json.dumps(proposal),json.dumps(context),json.dumps(audit),identity))
        from . import observation_semantics as semantics
        semantics.save(store,'conversation',oid,semantics.build(store,'conversation',source,source,item,history_audit=audit))
        catalog=getattr(store,'entity_catalog',None)
        if catalog:
            from .entities import Anchor
            catalog.bind('observation',oid,receipt['source_sha'],Anchor(domain='conversation',source_id=source['id'],
                start=start,end=start+len(quote),quote=quote,sha256=receipt['source_sha']),session_id=source['session_id'])
        if item['asserted'] and item['durable']:
            direct.append(oid)
    for row, oid in zip(reviews, direct, strict=True):
        store.db.execute('INSERT OR IGNORE INTO memory_formation_candidates VALUES(?,NULL,?,NULL)',
                         (row['id'], json.dumps([oid])))


def context_for_review(store, review_id):
    link = store.db.execute('SELECT * FROM memory_formation_candidates WHERE review_id=?', (review_id,)).fetchone()
    if not link:
        return None
    observations, sources = [], []
    for oid in json.loads(link['observation_ids_json']):
        row = store.db.execute('SELECT * FROM memory_observations WHERE id=?', (oid,)).fetchone()
        source = live_observation(store, row)
        observation=dict(row)
        from .formation_history import visible
        observation['history']=visible(store,row['id'])
        from .observation_semantics import load,projection
        data=load(store,'conversation',row['id'])
        observation['semantics']=projection(data) if data else None
        observations.append(observation)
        if source['id'] not in {s['id'] for s in sources}:
            sources.append(source)
    pattern = None
    target = json.loads(link['target_json']) if link['target_json'] else None
    if link['pattern_id']:
        row = store.db.execute('SELECT * FROM memory_pattern_versions WHERE id=?', (link['pattern_id'],)).fetchone()
        if not row or row['status'] not in {'pending', 'active', 'superseded', 'retired'}:
            raise ValueError('Pattern proposal is no longer available')
        pattern = dict(row)
        if set(json.loads(pattern['observation_ids_json'])) != set(json.loads(link['observation_ids_json'])):
            raise ValueError('Pattern evidence membership changed')
        if pattern['status'] == 'pending':
            head = store.db.execute("SELECT id FROM memory_pattern_versions WHERE bucket=? AND status='active' ORDER BY created_at DESC LIMIT 1", (pattern['bucket'],)).fetchone()
            if (head[0] if head else None) != pattern['previous_id']:
                raise ValueError('A newer pattern head superseded this proposal')
    if target and pattern and pattern['status'] == 'pending' and not store.memory_dependency_valid(target):
        raise ValueError('Evolution target changed after consolidation')
    return {'sources': sources, 'observations': observations, 'pattern': pattern, 'target': target}


def bind_support(store, memory_id, context, before_groups):
    """An inferred pattern needs the WHOLE evidence group, never its primary source alone."""
    if not context or not context['pattern']:
        return
    sources = context['sources']
    for source in sources:
        eligible_source(store, source['id'])
    groups = store.db.execute('SELECT id FROM memory_support_groups WHERE memory_id=?', (memory_id,)).fetchall()
    for row in groups:
        if row['id'] not in before_groups:
            store.db.execute('DELETE FROM memory_support_groups WHERE id=?', (row['id'],))
    lineage = digest(json.dumps([context['pattern']['id'],sorted((s['id'],digest(s['content']),s['session_id'],s['created_at']) for s in sources)]))
    gid = digest(memory_id + ':' + lineage)
    memory = store.memory(memory_id)
    store.db.execute('INSERT OR IGNORE INTO memory_support_groups VALUES(?,?,?,?,?,?)',
        (gid, memory_id, digest(memory['content']), lineage, now(), 'consolidated_pattern'))
    for source in sources:
        store.db.execute('INSERT OR IGNORE INTO memory_support_evidence VALUES(?,?,?,?,?,?)',
            (gid, source['id'], digest(source['content']), source['session_id'], source['created_at'], 'active'))
    store.db.execute('UPDATE memory_pattern_versions SET support_group_id=? WHERE id=?', (gid,context['pattern']['id']))


def published(store, review_id, memory_id):
    row = store.db.execute('SELECT pattern_id FROM memory_formation_candidates WHERE review_id=?', (review_id,)).fetchone()
    if not row or not row[0]:
        return
    pattern = store.db.execute('SELECT * FROM memory_pattern_versions WHERE id=?', (row[0],)).fetchone()
    store.db.execute("UPDATE memory_pattern_versions SET status='active',memory_id=? WHERE id=?", (memory_id, row[0]))
    if pattern['previous_id']:
        store.db.execute("UPDATE memory_pattern_versions SET status='superseded' WHERE id=?", (pattern['previous_id'],))
    store.db.execute('INSERT INTO memory_formation_events(pattern_id,action,reason,created_at) VALUES(?,?,?,?)',
        (row[0], pattern['action'], 'model_reviewed_with_complete_source_group', now()))


def maintenance(store, timestamp=None, observation_cutoff=None):
    timestamp = timestamp or now()
    with store._memory_transaction():
        if observation_cutoff:
            store.db.execute("UPDATE memory_observations SET state='archived' WHERE state='active' AND observed_at<?", (observation_cutoff,))
        rows = store.db.execute("SELECT id,review_id FROM memory_pattern_versions WHERE status='active'").fetchall()
        for row in rows:
            try:
                context_for_review(store, row['review_id'])
                pattern = store.db.execute('SELECT expires_at FROM memory_pattern_versions WHERE id=?', (row['id'],)).fetchone()
                reason = 'expired' if pattern[0] <= timestamp else ''
            except ValueError:
                reason = 'source_or_target_invalidated'
            if reason:
                store.db.execute("UPDATE memory_pattern_versions SET status='retired' WHERE id=?", (row['id'],))
                store.db.execute('INSERT INTO memory_formation_events(pattern_id,action,reason,created_at) VALUES(?,?,?,?)',
                                  (row['id'], 'RETIRE', reason, timestamp))


def pattern_available_sql(alias):
    from .source_support import group_valid_sql
    from .formation_history import pattern_live_sql
    return (f"({alias}.source!='consolidated_pattern' OR EXISTS(SELECT 1 FROM memory_pattern_versions p "
            f"JOIN memory_support_groups pg ON pg.id=p.support_group_id WHERE p.memory_id={alias}.id "
            "AND p.status='active' AND p.expires_at>strftime('%Y-%m-%dT%H:%M:%f+00:00','now') "
            f"AND {group_valid_sql(alias,'pg')} AND {pattern_live_sql('p')} "
            "AND NOT EXISTS(SELECT 1 FROM json_each(p.observation_ids_json) j LEFT JOIN memory_observations o ON o.id=j.value "
            "WHERE o.id IS NULL OR o.state NOT IN ('active','archived'))))")
