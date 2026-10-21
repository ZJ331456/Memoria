"""Actual-account SQLite-copy access timings; never invents approved Claim data."""
from __future__ import annotations

import sqlite3
import time
from collections import Counter

from ..claims import AccessRequest, ClaimMemory, SLOTS, ViewRequest, canonical
from ..store import Store
from .records import RunRecorder


def longitudinal_eligibility(workspace):
    eligible, public, messages = 0,0,0
    source_root = workspace.path("research","sources")
    for directory in sorted(source_root.iterdir()) if source_root.exists() else []:
        if not directory.is_dir() or not (directory/"manifest.json").is_file():
            continue
        manifest, history = workspace.load(directory.name)
        messages += len(history)
        if manifest["kind"]=="public_dialogue_diagnostic":
            public += 1
            continue
        groups = {}
        for item in history:
            groups.setdefault(item["participant_id"],[]).append(item)
        for participant in manifest["participants"]:
            rows = groups.get(participant["id"],[])
            if "evaluation" not in participant["consent"]["purposes"]:
                continue
            if len({row["conversation_id"] for row in rows})>=2 and all(row.get("observed_at") and row.get("speaker_id") for row in rows):
                eligible += 1
    return {"eligible_continuous_participants":eligible,"public_diagnostic_bundles":public,
            "source_messages":messages,"old_archives_read":False,"human_evaluation":"deferred by user",
            "long_term_effect":None,"reason":"no consented verified continuous histories" if not eligible else "continuous source available; dedicated replay protocol still required"}


def benchmark_access(workspace,settings,*,repeats=3,iterations=50):
    if type(repeats) is not int or not 1<=repeats<=20 or type(iterations) is not int or not 1<=iterations<=500:
        raise ValueError("Invalid access benchmark dimensions")
    from .runner import quantiles
    source = workspace.path("memory","memoria.db")
    if not source.is_file():
        raise ValueError("Actual account memory database missing")
    eligibility = longitudinal_eligibility(workspace)
    recorder = RunRecorder(workspace.path("experiments"),settings.root,datasets=[],models={},
        protocol={"owner_user_id":workspace.owner,"mode":"access_benchmark","repeats":repeats,"iterations":iterations,
                  "scope":"actual account snapshot; operational timings, not semantic or companion quality",
                  "budget_chars":16000,"ttl_seconds":86400,"concurrency":1,
                  "orders":[["uncached","cached","invalidated"],["cached","invalidated","uncached"],["invalidated","uncached","cached"]],
                  "human_evaluation":"deferred by user","production_writes":0},seed=None)
    snapshot = recorder.path/"account-snapshot.db"
    readonly = sqlite3.connect(source.resolve().as_uri()+"?mode=ro",uri=True)
    copied = sqlite3.connect(snapshot)
    try:
        readonly.backup(copied)
    finally:
        readonly.close()
        copied.close()
    store = Store(snapshot,vector_backend="json")
    counters,latencies,maintenance,failures = Counter(),{}, {},0
    fatal,metrics = None,None
    try:
        claims = ClaimMemory(store,workspace.owner)
        counts = {"sessions":store.overview()["sessions"],"messages":store.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                  "memories":store.overview()["memories"],"claims":store.db.execute("SELECT COUNT(*) FROM claim_roots").fetchone()[0]}
        approved = store.db.execute("SELECT COUNT(*) FROM claim_revisions WHERE status='approved'").fetchone()[0]
        for repeat in range(repeats):
            order = recorder.manifest["protocol"]["orders"][repeat%3]
            for predicate in SLOTS:
                request = AccessRequest(predicates=[predicate],budget_chars=16000)
                with store.memory_transaction():
                    reference = claims._select(request)
                expected = canonical(reference)
                for mode in order:
                    with store.memory_transaction():
                        store.db.execute("DELETE FROM claim_views")
                    if mode=="cached":
                        build_started = time.perf_counter()
                        claims.compile(ViewRequest(**request.model_dump(),ttl_seconds=86400))
                        maintenance.setdefault("cached_compile_ms",[]).append((time.perf_counter()-build_started)*1000)
                    for index in range(iterations):
                        if mode=="uncached":
                            with store.memory_transaction():
                                store.db.execute("DELETE FROM claim_views")
                        elif mode=="invalidated":
                            build_started = time.perf_counter()
                            claims.compile(ViewRequest(**request.model_dump(),ttl_seconds=86400))
                            with store.memory_transaction():
                                store.db.execute("UPDATE claim_generations SET generation=generation+1 WHERE user_id=? AND subject_id='self' AND predicate=?",(workspace.owner,predicate))
                            maintenance.setdefault("compile_and_invalidate_ms",[]).append((time.perf_counter()-build_started)*1000)
                        started = time.perf_counter()
                        result = claims.access(request)
                        elapsed = (time.perf_counter()-started)*1000
                        match = canonical(result["payload"])==expected
                        failures += not match
                        latencies.setdefault(mode,[]).append(elapsed)
                        counters[mode+"_"+result["path"]] += 1
                        recorder.append("cases.jsonl",{"id":f"{repeat}:{predicate}:{mode}:{index}","repeat":repeat,
                            "predicate":predicate,"mode":mode,"status":"success" if match else "error",
                            "path":result["path"],"view_used":bool(result["view_id"]),"payload_equivalent":match,
                            "elapsed_ms":elapsed,"complete":result["payload"]["complete"]})
        metrics = {"errors":failures,"requested_cases":repeats*iterations*len(SLOTS)*3,"account_snapshot_counts":counts,
                   "approved_revisions":approved,"actual_model_calls":0,"production_writes":0,
                   "access_latency":{key:quantiles(value) for key,value in latencies.items()},
                   "maintenance_latency":{key:quantiles(value) for key,value in maintenance.items()},
                   "path_counts":dict(counters),"longitudinal_eligibility":eligibility,
                   "positive_view_benefit_measured":bool(approved and any(key.endswith("_view") for key in counters)),
                   "interpretation":"Empty-state cache overhead only; no populated-view or long-term benefit claim" if not approved else "Actual snapshot workload; semantic human quality not evaluated",
                   "integrity":store.db.execute("PRAGMA integrity_check").fetchone()[0],
                   "foreign_key_errors":len(store.db.execute("PRAGMA foreign_key_check").fetchall())}
    except BaseException as error:
        fatal = type(error).__name__
        raise
    finally:
        store.close()
        # SQLite checkpoint/close must precede the final artifact digests.
        recorder.finish(metrics or {"errors":1,"production_writes":0,"quality":"not evaluated"},error=fatal)
    return recorder.path
