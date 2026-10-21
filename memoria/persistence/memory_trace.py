"""SQLite lifecycle tracking and read-only lineage projections.

Queries are intentionally account-local and execute under the original Store lock.
No generic SQL port is exposed to observability or hosts.
"""

import hashlib
import json
from ..observability.memory_trace import STAGES, fingerprint


def install(store):
    # Individual statements preserve any caller-owned transaction.
    store.db.execute("""CREATE TABLE IF NOT EXISTS memory_trace_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, stage TEXT NOT NULL, table_name TEXT NOT NULL,
        entity_id TEXT NOT NULL, operation TEXT NOT NULL, recorded_at TEXT NOT NULL)""")
    store.db.execute("""CREATE TABLE IF NOT EXISTS memory_trace_epochs (
        stage TEXT PRIMARY KEY, generation INTEGER NOT NULL)""")
    tables = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for stage, names in STAGES.items():
        store.db.execute("INSERT OR IGNORE INTO memory_trace_epochs VALUES(?,0)", (stage,))
        for table in names:
            if table not in tables:
                continue
            columns = [r[1] for r in store.db.execute(f"PRAGMA table_info({table})")]
            key = next(
                (
                    c
                    for c in (
                        "id",
                        "memory_id",
                        "message_id",
                        "user_id",
                        "observation_id",
                        "revision_ref",
                    )
                    if c in columns
                ),
                columns[0],
            )
            for operation in ("INSERT", "UPDATE", "DELETE"):
                alias = "OLD" if operation == "DELETE" else "NEW"
                when = (
                    " WHEN " + " OR ".join(f"OLD.{c} IS NOT NEW.{c}" for c in columns)
                    if operation == "UPDATE"
                    else ""
                )
                store.db.execute(
                    f"""CREATE TRIGGER IF NOT EXISTS trace_{table}_{operation.lower()}
                    AFTER {operation} ON {table}{when} BEGIN
                    INSERT INTO memory_trace_events(stage,table_name,entity_id,operation,recorded_at)
                    VALUES('{stage}','{table}',CAST({alias}.{key} AS TEXT),'{operation.lower()}',
                           strftime('%Y-%m-%dT%H:%M:%fZ','now'));
                    UPDATE memory_trace_epochs SET generation=generation+1 WHERE stage='{stage}'; END"""
                )


def watermark(store):
    with store.memory_transaction():
        return {
            "schema_version": 1,
            "origin": "since_trace_schema_install; not lifetime history",
            "event_seq": store.db.execute(
                "SELECT COALESCE(MAX(seq),0) FROM memory_trace_events"
            ).fetchone()[0],
            "epochs": dict(store.db.execute("SELECT stage,generation FROM memory_trace_epochs")),
            "embedding_namespace": store.embedding_namespace,
            "jobs": dict(
                store.db.execute("SELECT status,COUNT(*) FROM memory_jobs GROUP BY status")
            ),
            "reviews": dict(
                store.db.execute("SELECT status,COUNT(*) FROM memory_reviews GROUP BY status")
            ),
        }


def source_lineage(kernel, references):
    """Current bounded metadata snapshot. No model, approval, source text or historic reconstruction."""
    if len(references) > 64:
        raise ValueError("At most 64 source references per lineage snapshot")
    store = kernel.store
    result = []

    def rows(sql, args):
        return [dict(r) for r in store.db.execute(sql, args)]

    with store.lock:
        for ref in references:
            domain, sid = ref["domain"], ref["id"]
            item = {
                "domain": domain,
                "id": sid,
                "available": False,
                "observations": [],
                "reviews": [],
                "published": [],
            }
            if domain == "memory":
                memory = store.memory(sid)
                if memory:
                    source = store.memory_source(sid)
                    item.update(
                        available=store.memory_dependency_valid(store.memory_dependency(memory)),
                        content_sha256=hashlib.sha256(memory["content"].encode()).hexdigest(),
                        status=memory["status"],
                        source_ref=source["message_id"] if source else None,
                    )
                    from ..memory.source_support import live_groups

                    groups = live_groups(store, sid)
                    item["support_groups"] = [
                        {
                            "id": g["id"],
                            "origin": g["origin"],
                            "message_ids": [e["message_id"] for e in g["evidence"]],
                        }
                        for g in groups
                    ]
                    refs = list(
                        dict.fromkeys(e["message_id"] for g in groups for e in g["evidence"])
                    )
                    item["support_lineage_truncated"] = len(refs) > 64
                    item["support_lineage"] = source_lineage(
                        kernel, [{"domain": "conversation", "id": mid} for mid in refs[:64]]
                    )
            elif domain == "conversation":
                source = store.message_source(sid)
                if source:
                    try:
                        from ..memory.formation_store import eligible_source

                        eligible_source(store, sid)
                        eligible = True
                    except ValueError:
                        eligible = False
                    origin = store.db.execute(
                        "SELECT channel,event_ref FROM message_origins WHERE message_id=?", (sid,)
                    ).fetchone()
                    item.update(
                        available=store.memory_dependency_valid(store.message_dependency(source)),
                        personal_claim_eligible=eligible,
                        role=source["role"],
                        session_id=source["session_id"],
                        recorded_at=source["created_at"],
                        source_sha256=hashlib.sha256(source["content"].encode()).hexdigest(),
                        origin=dict(origin) if origin else {"channel": "unknown"},
                    )
                    from ..memory.observation_semantics import message_clock, public_clock

                    item["source_clock"] = public_clock(message_clock(store, source))
                    item["jobs"] = rows(
                        "SELECT id,status,attempts,extracted_count,staged_count FROM memory_jobs WHERE source_ref=?",
                        (sid,),
                    )
                    observations = rows(
                        """SELECT id,run_id,category,topic,scope,polarity,start,end,asserted,durable,state,
                        source_sha,observed_at FROM memory_observations WHERE message_id=? ORDER BY rowid LIMIT 64""",
                        (sid,),
                    )
                    from ..memory.formation_history import visible

                    for observation in observations:
                        from ..memory.observation_semantics import load

                        observation["semantics"] = load(store, "conversation", observation["id"])
                        try:
                            observation["history"] = visible(store, observation["id"])
                        except ValueError:
                            observation["history"] = {"association": {"status": "stale"}}
                    item["observations"] = observations
                    for review in rows(
                        "SELECT id FROM memory_reviews WHERE source_ref=? ORDER BY rowid LIMIT 64",
                        (sid,),
                    ):
                        review = store.memory_review(review["id"])
                        audit = {
                            k: review.get(k)
                            for k in ("id", "status", "applied_memory_id", "applied_claim_id")
                        }
                        assessment = review.get("assessment")
                        if assessment:
                            assessment = dict(assessment)
                            proposal = assessment.get("structured_proposal")
                            if proposal:
                                assessment["structured_proposal"] = {
                                    k: v
                                    for k, v in proposal.items()
                                    if k not in {"evidence", "value"}
                                } | {
                                    "value_sha256": fingerprint(proposal.get("value")),
                                    "evidence": [
                                        {k: v for k, v in e.items() if k != "quote"}
                                        for e in proposal["evidence"]
                                    ],
                                }
                        audit["assessment"] = assessment
                        item["reviews"].append(audit)
                    for review in item["reviews"]:
                        if review["applied_memory_id"]:
                            memory = store.memory(review["applied_memory_id"])
                            if memory:
                                item["published"].append(
                                    {
                                        "kind": "memory",
                                        "id": memory["id"],
                                        "status": memory["status"],
                                        "content_sha256": fingerprint(memory["content"]),
                                        "origin": memory["source"],
                                    }
                                )
                    for row in rows(
                        """SELECT DISTINCT r.id,r.claim_id,r.revision,r.status,r.recorded_at,r.data_json
                        FROM claim_sources s JOIN claim_revisions r ON r.id=s.revision_id WHERE s.message_id=?
                        ORDER BY r.recorded_at,r.revision LIMIT 64""",
                        (sid,),
                    ):
                        data = json.loads(row.pop("data_json"))
                        row["kind"] = "claim"
                        head = kernel.claims.get(row["claim_id"])
                        row["current"] = head["revision_id"] == row["id"]
                        row["admission_origin"] = (
                            "llm"
                            if any(
                                (r.get("assessment") or {}).get("applied_claim_revision_id")
                                == row["id"]
                                for r in item["reviews"]
                            )
                            else "unknown_from_legacy_metadata"
                        )
                        row["source_available"] = (
                            head["source_available"] if row["current"] else None
                        )
                        row["identity"] = {
                            k: head.get(k) for k in ("subject_id", "predicate", "scope")
                        }
                        row["identity"]["polarity"] = data.get("polarity")
                        row["value_sha256"] = fingerprint(data.get("value"))
                        item["published"].append(row)
                    for pattern in rows(
                        """SELECT DISTINCT p.id,p.memory_id,p.status,p.action,p.previous_id,p.expires_at
                        FROM memory_pattern_versions p JOIN json_each(p.observation_ids_json) j
                        JOIN memory_observations o ON o.id=j.value WHERE o.message_id=? LIMIT 64""",
                        (sid,),
                    ):
                        item["published"].append({"kind": "pattern", **pattern})
            elif domain == "library":
                source = store.db.execute(
                    "SELECT * FROM library_sources WHERE id=?", (sid,)
                ).fetchone()
                if source:
                    raw = kernel.library._revision(source["head_id"])
                    from ..memory.observation_semantics import library_clock, public_clock

                    item["source_clock"] = public_clock(
                        library_clock(kernel.library, dict(source), raw)
                    )
                    item.update(
                        available=source["status"] == "active",
                        kind=source["kind"],
                        authorship=source["authorship"],
                        scope=source["scope"],
                        session_id=source["session_id"],
                        revision_id=raw["id"],
                        source_sha256=raw["sha"],
                        recorded_at=raw["created_at"],
                    )
                    item["observations"] = rows(
                        """SELECT o.id,o.card_id,o.revision_id,o.group_json,c.status,c.start,c.end,
                        o.subject_start,o.subject_end FROM library_observations o JOIN library_cards c ON c.id=o.card_id
                        WHERE o.source_id=? ORDER BY o.rowid LIMIT 64""",
                        (sid,),
                    )
                    from ..memory.observation_semantics import load

                    for observation in item["observations"]:
                        observation["semantics"] = load(store, "library", observation["id"])
                    item["reviews"] = [
                        {"id": r["card_id"], "status": r["status"]} for r in item["observations"]
                    ]
                    item["published"] = [
                        {
                            "kind": "source_observation",
                            "id": r["id"],
                            "revision_id": r["revision_id"],
                            "available": kernel.library.formation.card_available(r["card_id"]),
                        }
                        for r in item["observations"]
                    ]
                    for version in rows(
                        """SELECT DISTINCT v.id,v.root_id,v.previous_id,v.action,v.status,v.created_at,v.support_json
                        FROM library_consolidation_versions v JOIN json_each(v.context_json) j
                        WHERE json_extract(j.value,'$.id')=? ORDER BY v.created_at LIMIT 64""",
                        (sid,),
                    ):
                        root = store.db.execute(
                            "SELECT head_id,status FROM library_consolidations WHERE id=?",
                            (version["root_id"],),
                        ).fetchone()
                        item["published"].append(
                            {
                                "kind": "consolidation",
                                **version,
                                "support_groups": json.loads(version["support_json"]),
                                "current": bool(root and root["head_id"] == version["id"]),
                                "root_status": root["status"] if root else None,
                            }
                        )
                    item["calls"] = rows(
                        "SELECT id,stage,model,usage_json,status,duration_ms FROM library_calls WHERE revision_id=? LIMIT 128",
                        (raw["id"],),
                    )
            else:
                raise ValueError("Unknown lineage source domain")
            item["snapshot_scope"] = "current metadata; no retroactive source reconstruction"
            result.append(item)
    return result
