"""Account-local lifecycle watermarks and frozen-pool diagnostics, never quality gold."""
from __future__ import annotations

import hashlib
import json

STAGES = {
    "source": ("messages", "memory_source_revocations", "dataset_imports"),
    "extraction": ("memory_jobs",),
    "review": ("memory_reviews",),
    "memory": ("memories", "memory_operations", "memory_validity_intervals", "memory_source_dependencies",
               "memory_support_groups", "memory_support_evidence"),
    "index": ("memory_embeddings", "memory_embedding_chunks", "experience_versions", "experience_chunks"),
    "claim": ("claim_roots", "claim_revisions", "claim_generations"),
    "view": ("claim_views",),
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


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
            key = next((c for c in ("id", "memory_id", "message_id", "user_id") if c in columns), columns[0])
            for operation in ("INSERT", "UPDATE", "DELETE"):
                alias = "OLD" if operation == "DELETE" else "NEW"
                when = " WHEN " + " OR ".join(f"OLD.{c} IS NOT NEW.{c}" for c in columns) if operation == "UPDATE" else ""
                store.db.execute(f"""CREATE TRIGGER IF NOT EXISTS trace_{table}_{operation.lower()}
                    AFTER {operation} ON {table}{when} BEGIN
                    INSERT INTO memory_trace_events(stage,table_name,entity_id,operation,recorded_at)
                    VALUES('{stage}','{table}',CAST({alias}.{key} AS TEXT),'{operation.lower()}',
                           strftime('%Y-%m-%dT%H:%M:%fZ','now'));
                    UPDATE memory_trace_epochs SET generation=generation+1 WHERE stage='{stage}'; END""")


def watermark(store):
    with store._memory_transaction():
        return {"schema_version": 1, "origin": "since_trace_schema_install; not lifetime history",
            "event_seq": store.db.execute("SELECT COALESCE(MAX(seq),0) FROM memory_trace_events").fetchone()[0],
            "epochs": dict(store.db.execute("SELECT stage,generation FROM memory_trace_epochs")),
            "embedding_namespace": store.embedding_namespace,
            "jobs": dict(store.db.execute("SELECT status,COUNT(*) FROM memory_jobs GROUP BY status")),
            "reviews": dict(store.db.execute("SELECT status,COUNT(*) FROM memory_reviews GROUP BY status"))}


def capture(request, result, owner, before, after, coverage_by_id):
    # Full candidates remain in the existing response exactly once; no second private text copy.
    trace = {"schema_version": 1, "owner_user_id": owner, "request": request,
        "before": before, "after": after, "coverage_by_id": coverage_by_id,
        "candidate_pool_sha256": fingerprint(result["candidates"]),
        "requirements_sha256": fingerprint(result["requirements"]),
        "selection_sha256": fingerprint(result["selection"]),
        "state_sha256": fingerprint(result["state"]),
        "request_sha256": fingerprint(request),
        "changed_stages": [s for s in before["epochs"] if before["epochs"][s] != after["epochs"][s]],
        "quality": "unscored", "gold_read": False,
        "replay_scope": "frozen candidate selection; not rerunning extraction or reader"}
    trace["trace_sha256"] = fingerprint(trace)
    return trace


def replay(packet, owner, *, budgets=None):
    from ..memory.evidence import EvidenceBudget
    from ..memory.evidence_selector import select_evidence
    trace = packet["production_trace"]
    if trace["owner_user_id"] != owner:
        raise ValueError("Production capture belongs to another account")
    if trace["schema_version"] != 1 or fingerprint({k:v for k,v in trace.items() if k != "trace_sha256"}) != trace["trace_sha256"]:
        raise ValueError("Production trace fingerprint mismatch")
    for key, value in (("candidate_pool", packet["candidates"]), ("requirements", packet["requirements"]),
                       ("selection", packet["selection"]), ("state", packet["state"]), ("request",trace["request"])):
        if fingerprint(value) != trace[key+"_sha256"]:
            raise ValueError("Production capture fingerprint mismatch: "+key)
    request = trace["request"]
    if request["budget_unit"] == "reader_tokens":
        raise ValueError("Offline replay needs the original trusted reader tokenizer; tokens cannot be guessed")
    if set(trace["coverage_by_id"]) != {c["id"] for c in packet["candidates"]}:
        raise ValueError("Captured coverage does not match candidate IDs")
    pool = [{**c, "covers":trace["coverage_by_id"][c["id"]]} for c in packet["candidates"]]
    limits = budgets or [request.get("evidence_budget") or request["budget_chars"]]
    if len(limits) > 8 or any(type(b) is not int or not 256 <= b <= 16000 for b in limits):
        raise ValueError("Use at most eight evidence budgets in 256..16000")
    result = {}
    for limit in limits:
        budget = EvidenceBudget(limit, request["budget_unit"])
        result[str(limit)] = {strategy: select_evidence(pool, packet["requirements"]["facets"], limit,
            strategy, request["top_k"], budget=budget)
            for strategy in ("topk", "coverage", "mmr", "evidence_coverage")}
    return {"selections":result, "candidate_pool_sha256":trace["candidate_pool_sha256"],
            "model_calls":0, "automatic_memory_writes":0, "quality":"unscored",
            "budget_scope":"complete evidence JSON; excludes reader/system/output"}


def attribute(packet, *, required_source_ids=None, source_inventory=None, reader=None, answer_correct=None):
    """Post-hoc diagnostic only. Label IDs NEVER enter retrieval or selection."""
    required = set(required_source_ids or [])
    if not required:
        return {"stage":"unlabelled", "quality":"unknown", "semantic_sufficiency":None}
    retrieved = {s for c in packet["candidates"] for s in c["source_ids"]}
    presented = {s for c in packet["selection"]["payload"]["evidence"] for s in c["source_ids"]}
    if source_inventory is not None and not required <= set(source_inventory):
        stage = "source_not_written_or_unavailable"
    elif packet.get("state") and not packet["state"]["payload"]["complete"]:
        stage = "state_not_approved_complete_or_applicable"
    elif not required <= retrieved:
        stage = "not_retrieved"
    elif not required <= presented:
        stage = "candidate_not_presented"
    elif reader is None:
        stage = "presented_reader_not_run"
    elif not reader.get("validation", {}).get("valid", True):
        stage = "reader_contract_or_support_failure"
    elif answer_correct is False:
        stage = "reader_answer_or_support_failure"
    elif reader.get("status") != "answer":
        stage = "reader_abstained_or_clarified"
    else:
        stage = "answered_correct" if answer_correct is True else "answered_unscored"
    return {"stage":stage, "required_source_count":len(required),
            "missing_retrieved":sorted(required-retrieved), "missing_presented":sorted(required-presented),
            "semantic_sufficiency":None, "interpretation":"source-ID coverage diagnostic, not semantic gold"}
