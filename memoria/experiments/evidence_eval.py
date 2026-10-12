"""Replay a captured prefix-only pool; inference cannot access gold evidence."""
from __future__ import annotations
import hashlib
import time
from datetime import datetime
from pathlib import Path

from .history import canonical
from .runner import load_run
from .records import RunRecorder, digest_file
from ..memory.evidence import EvidenceBudget, requirements_for
from ..memory.evidence.requirements import coverage
from ..memory.evidence_selector import select_evidence
from ..memory.temporal_query import TemporalQueryParser


def replay_selection(workspace, source_run, *, budget=1024, unit="chars", root: Path):
    if unit not in {"chars","utf8_bytes"} or not 256 <= budget <= 16000:
        raise ValueError("invalid selection budget")
    source_path, source_manifest, source_cases = load_run(workspace, source_run)
    old = source_manifest["protocol"]
    if old["mode"] != "retrieval" or old.get("evidence_selector", "legacy") != "legacy":
        raise ValueError("replay requires an original retrieval pool, not selected evidence")
    protocol, inputs, input_path = workspace.protocol(old["protocol_id"])
    if any(protocol[k] != old[k] for k in ("input_sha256","gold_sha256","history_sha256")):
        raise ValueError("captured pool and frozen inputs differ")
    by_id = {c["id"]:c for c in source_cases}
    if len(by_id) != len(source_cases) or set(by_id) != {c["id"] for c in inputs}:
        raise ValueError("missing, duplicate or foreign captured cases")
    reference = datetime.fromisoformat(source_manifest["started_at_utc"])
    # Build once. All selectors see byte-identical requirements, data, IDs and scores.
    pools = []
    for case in inputs:
        row, history = by_id[case["id"]], {m["id"]:m for m in case["history"]}
        ids = row.get("retrieved_ids", [])
        if len(ids) != len(set(ids)) or set(ids)-set(history):
            raise ValueError("future/foreign/duplicate source in captured pool")
        parsed = TemporalQueryParser().parse(case["question"], reference)
        requirements = requirements_for(case["question"], parsed)
        diagnostics = {d["message_id"]:d for d in row.get("retrieval_diagnostics",{}).get("candidates",[])}
        pool = []
        for rank, mid in enumerate(ids,1):
            m, d = history[mid], diagnostics.get(mid,{})
            if d.get("content_sha256") and hashlib.sha256(m["content"].encode()).hexdigest() != d["content_sha256"]:
                raise ValueError("captured source SHA differs")
            score = d.get("rrf_score") if old["baseline"] == "B2_rrf" else d.get("dense_score")
            item = {"id":mid,"kind":"source","score":score if score is not None else 1/rank,"source_ids":[mid],
                    "data":{**m,"text":m["content"]}}
            item["covers"] = coverage(item, requirements, parsed)
            pool.append(item)
        pools.append((case, row, pool, requirements))
    outputs = []
    for strategy in ("topk","coverage","mmr","evidence_coverage"):
        recorder = RunRecorder(workspace.path("experiments"), root, datasets=[input_path,source_path/"cases.jsonl"], models={},
            protocol={**protocol,"owner_user_id":workspace.owner,"mode":"retrieval","baseline":old["baseline"],
                      "source_run":source_run,"source_cases_sha256":digest_file(source_path/"cases.jsonl"),
                      "scope":"captured fixed-pool selection diagnostic; no reader/embedding rerun",
                      "history_limit":old["history_limit"],"context_char_budget":budget,"budget_unit":unit,
                      "evidence_selector":strategy,"budget_scope":"complete evidence JSON; excludes question/system/output",
                      "requirements_origin":"query/parser only; no gold loaded","reference_time":reference.isoformat()}, seed=None)
        recorder.manifest["case_ids"]=[c["id"] for c in inputs]
        errors, statuses, durations = 0, {}, []
        for case, source, pool, requirements in pools:
            started=time.perf_counter()
            if source["status"] not in {"success","empty"}:
                row={"id":case["id"],"status":"error","error":"captured retrieval failed","presented_ids":[]}
                errors+=1
            else:
                selection=select_evidence(pool,requirements["facets"],budget,strategy,old["history_limit"],budget=EvidenceBudget(budget,unit))
                row={"id":case["id"],"status":"success" if selection["payload"]["evidence"] else "empty","error":None,
                     "retrieved_ids":[c["id"] for c in pool],"presented_ids":[c["id"] for c in selection["payload"]["evidence"]],
                     "evidence_candidates":pool,"requirements":requirements,"evidence_selection":selection,
                     "candidate_pool_sha256":hashlib.sha256(canonical(pool).encode()).hexdigest()}
            row["elapsed_ms"]=round((time.perf_counter()-started)*1000,3)
            durations.append(row["elapsed_ms"]); statuses[row["status"]]=statuses.get(row["status"],0)+1
            recorder.append("cases.jsonl",row)
        recorder.finish({"requested_cases":len(inputs),"errors":errors,"statuses":statuses,"calls":0,
                         "usage_known_calls":0,"usage_unknown_calls":0,"quality":"unscored",
                         "automatic_memory_writes":0,"selection_elapsed_ms":sum(durations)})
        outputs.append(recorder.path)
    return outputs
