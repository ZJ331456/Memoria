"""Versioned ordinary-memory support. Whole-source groups, not semantic independence."""
from __future__ import annotations
import hashlib
from ..store import now


def digest(text):return hashlib.sha256(text.encode()).hexdigest()


def install(store):
    store.db.execute("""CREATE TABLE IF NOT EXISTS memory_support_groups (
        id TEXT PRIMARY KEY, memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
        memory_sha256 TEXT NOT NULL, lineage_id TEXT NOT NULL, created_at TEXT NOT NULL,
        origin TEXT NOT NULL, UNIQUE(memory_id,lineage_id))""")
    store.db.execute("""CREATE TABLE IF NOT EXISTS memory_support_evidence (
        group_id TEXT NOT NULL REFERENCES memory_support_groups(id) ON DELETE CASCADE,
        message_id TEXT NOT NULL, content_sha256 TEXT NOT NULL, session_id TEXT NOT NULL,
        observed_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active',
        PRIMARY KEY(group_id,message_id))""")
    store.db.execute("CREATE INDEX IF NOT EXISTS idx_support_message ON memory_support_evidence(message_id)")
    for event in ('DELETE','UPDATE'):
        when = (" WHEN OLD.content IS NOT NEW.content OR OLD.role IS NOT NEW.role "
                "OR OLD.session_id IS NOT NEW.session_id OR OLD.created_at IS NOT NEW.created_at") if event=='UPDATE' else ''
        store.db.execute(f"""CREATE TRIGGER IF NOT EXISTS support_source_{event.lower()}
            BEFORE {event} ON messages{when} BEGIN
            UPDATE memory_support_evidence SET state='invalidated' WHERE message_id=OLD.id; END""")
    # Only the originally tracked dependency can be migrated: old reinforcement
    # operations did not capture source versions and cannot create substitute proof.
    store.db.execute("""INSERT OR IGNORE INTO memory_support_groups
        SELECT 'legacy:'||m.id,m.id,memoria_sha256(m.content),'legacy:'||m.id,m.created_at,'legacy_primary'
        FROM memories m JOIN memory_source_dependencies d ON d.memory_id=m.id
        WHERE NOT EXISTS(SELECT 1 FROM memory_support_groups g WHERE g.memory_id=m.id)""")
    store.db.execute("""INSERT OR IGNORE INTO memory_support_evidence
        SELECT g.id,d.message_id,CASE WHEN u.content IS NULL THEN '' ELSE memoria_sha256(u.content) END,COALESCE(u.session_id,''),
               COALESCE(u.created_at,''),CASE WHEN d.state='active' AND u.role='user' THEN 'active' ELSE 'invalidated' END
        FROM memory_support_groups g JOIN memory_source_dependencies d ON d.memory_id=g.memory_id
        LEFT JOIN messages u ON u.id=d.message_id WHERE g.origin='legacy_primary'""")


def group_valid_sql(memory_alias='m',group_alias='g',excluded=()):
    from .experience import personal_source_sql
    # A group needs every member, same source version and same memory body.
    missing=("e.state!='active' OR u.id IS NULL OR u.role!='user' OR "
        "e.content_sha256!=memoria_sha256(COALESCE(u.content,'')) OR e.session_id!=u.session_id OR e.observed_at!=u.created_at "
        "OR NOT ("+personal_source_sql('u')+")")
    if excluded:
        missing += ' OR e.message_id IN ('+','.join('?' for _ in excluded)+')'
    return (f"{group_alias}.memory_id={memory_alias}.id AND {group_alias}.memory_sha256=memoria_sha256({memory_alias}.content) "
        f"AND EXISTS(SELECT 1 FROM memory_support_evidence e WHERE e.group_id={group_alias}.id) "
        f"AND NOT EXISTS(SELECT 1 FROM memory_support_evidence e LEFT JOIN messages u ON u.id=e.message_id "
        f"WHERE e.group_id={group_alias}.id AND ({missing}))")


def availability_sql(alias='m'):
    legacy=(f"NOT EXISTS(SELECT 1 FROM memory_source_dependencies d LEFT JOIN messages u ON u.id=d.message_id "
        f"WHERE d.memory_id={alias}.id AND (d.state!='active' OR u.id IS NULL OR u.role!='user')) "
        f"AND NOT EXISTS(SELECT 1 FROM memory_source_revocations r WHERE r.message_id={alias}.source_ref)")
    return (f"((NOT EXISTS(SELECT 1 FROM memory_support_groups g WHERE g.memory_id={alias}.id) AND {legacy}) "
            f"OR EXISTS(SELECT 1 FROM memory_support_groups g WHERE {group_valid_sql(alias)}))")


def add_group(store,memory_id,source_ref,*,origin):
    if origin=='exact_reinforcement' and not store.db.execute(
            'SELECT 1 FROM memory_source_dependencies WHERE memory_id=? UNION ALL SELECT 1 FROM memory_support_groups WHERE memory_id=?',
            (memory_id,memory_id)).fetchone():
        return False  # Do not turn an untracked manual note into a source-only fact.
    source=store.message_source(source_ref) if source_ref else None
    if not source or source['role']!='user' or store.source_revoked(source_ref):return False
    memory=store.db.execute('SELECT content FROM memories WHERE id=?',(memory_id,)).fetchone()
    lineage=digest(source['content'])
    # Compare raw fingerprints too for conservative legacy copy handling.
    if store.db.execute("""SELECT 1 FROM memory_support_groups g JOIN memory_support_evidence e ON e.group_id=g.id
        WHERE g.memory_id=? AND (g.lineage_id=? OR e.content_sha256=?)""",(memory_id,lineage,lineage)).fetchone():return False
    if store.db.execute('SELECT COUNT(*) FROM memory_support_groups WHERE memory_id=?',(memory_id,)).fetchone()[0]>=16:
        raise ValueError('Ordinary memory support is limited to 16 groups; explicit consolidation required')
    gid=digest(memory_id+':'+lineage)
    store.db.execute('INSERT INTO memory_support_groups VALUES(?,?,?,?,?,?)',
        (gid,memory_id,digest(memory[0]),lineage,now(),origin))
    store.db.execute('INSERT INTO memory_support_evidence VALUES(?,?,?,?,?,?)',
        (gid,source_ref,lineage,source['session_id'],source['created_at'],'active'))
    return True


def live_groups(store,memory_id,*,excluded=()):
    groups=store.db.execute('SELECT g.* FROM memory_support_groups g JOIN memories m ON m.id=g.memory_id WHERE m.id=? AND '+
        group_valid_sql(excluded=excluded)+' ORDER BY g.created_at,g.id',(memory_id,*excluded)).fetchall()
    return [{**dict(g),'evidence':[dict(e) for e in store.db.execute(
        'SELECT * FROM memory_support_evidence WHERE group_id=? ORDER BY message_id',(g['id'],))]} for g in groups]


def source_for(store,memory_id):
    # Public writers currently produce whole-message singleton groups; never
    # turn a future multi-message conjunction into an independently valid fragment.
    for group in live_groups(store,memory_id):
        if len(group['evidence'])==1:
            return store.message_source(group['evidence'][0]['message_id'])
    if not store.db.execute('SELECT 1 FROM memory_support_groups WHERE memory_id=?',(memory_id,)).fetchone():
        row=store.db.execute('SELECT source_ref FROM memories WHERE id=?',(memory_id,)).fetchone()
        return store.message_source(row[0]) if row and row[0] else None
    return None
