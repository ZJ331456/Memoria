"""Export only whitelisted aggregates from a frozen diagnostic run (never raw QA)."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from memoria.experiments.deepseek_cache import cost_bounds
from memoria.experiments.eval_schema import ArmResult, summarize
from memoria.llm.deepseek_cache import cache_summary


def lines(path):
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read_cases(run, arms, ids):
    """Discard private question, answer, evidence and free-form reasons immediately."""
    result = {arm: {} for arm in arms}
    for row in lines(Path(run) / "cases.jsonl"):
        arm, ident = row["arm"], row["id"]
        if arm not in result or ident not in ids or ident in result[arm]:
            raise ValueError("Unexpected or duplicate checkpoint: refusing filtered publication")
        case = ArmResult(**{key: row[key] for key in (
            "id", "arm", "group", "correct", "score", "error", "latency_ms", "answer_status"
        ) if key in row})
        # Keep only numeric/boolean diagnostics; locators and quoted summaries stay private.
        extra = row.get("extras", {})
        stages = ("initial_candidates", "final_candidates", "gate_bound_evidence", "reader_evidence", "published_citations")
        pipeline = extra.get("pipeline", {})
        case.extras["pipeline"] = {"stages": {name: {
            key: int(value.get(key, 0)) for key in ("records", "consolidated_summaries",
                "observation_hint_records", "observation_ids", "visible_observation_summaries")
        } for name, value in pipeline.get("stages", {}).items() if name in stages}}
        if gate := pipeline.get("gate"):
            case.extras["pipeline"]["gate"] = {
                "sufficient": bool(gate.get("sufficient")),
                "unassessed_candidates": int(gate.get("unassessed_candidates", 0))}
        if repair := pipeline.get("answer_repair"):
            case.extras["pipeline"]["answer_repair"] = {
                key: bool(repair.get(key)) for key in (
                    "attempted", "accepted", "question_requirement_coverage_verified")}
        gold = extra.get("posthoc_gold", {})
        case.extras["posthoc_gold"] = {
            "gold_defined": bool(gold.get("gold_defined")),
            "all_gold_visible": bool(gold.get("all_gold_visible")),
            "unavailable_gold_ids": bool(gold.get("unavailable_gold_ids")),
            "stages": {name: {"all_gold_ids_present": bool(value.get("all_gold_ids_present"))}
                for name, value in gold.get("stages", {}).items() if name in stages}}
        result[arm][ident] = case
    if any(set(rows) != set(ids) for rows in result.values()):
        raise ValueError("Every planned ID must have a checkpoint, including not_run failures")
    return result


def paired(before, after):
    counts = Counter()
    for ident, old in before.items():
        new = after[ident]
        if old.group != new.group:
            raise ValueError("Category drift in paired results")
        if old.group == "5":
            continue
        if old.error or new.error or old.score is None or new.score is None:
            counts["unknown"] += 1
        elif old.score == 1 and new.score == 1:
            counts["both_correct"] += 1
        elif old.score == 1:
            counts["regressed"] += 1
        elif new.score == 1:
            counts["improved"] += 1
        else:
            counts["both_incorrect"] += 1
    return {key: counts[key] for key in (
        "both_correct", "both_incorrect", "improved", "regressed", "unknown")}


def publish(run, registration, *, previous=None, primary_only=False):
    run, registration = Path(run), Path(registration)
    declared = json.loads(registration.read_text(encoding="utf-8"))
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    if manifest["status"] not in {"completed", "failed"}:
        raise ValueError("Run must have stopped before publication")
    protocol = manifest["protocol"]
    if protocol["protocol"] != declared["protocol"] or protocol["item_ids"] != declared["item_ids"]:
        raise ValueError("Frozen registration differs from run")
    if (protocol["dataset_sha256"] != declared["source_sha256"] or
            protocol["judge_prompt_sha256"] != declared["judge_prompt_sha256"] or
            protocol["model"] != declared["backbone"]):
        raise ValueError("Dataset, model or judge differs from registration")
    for declared_key, runtime_key in (
            ("arm_order", "arm_order"), ("arm_configuration", "arm_configuration"),
            ("kernel_evidence_chars", "kernel_budget_chars"),
            ("reader_max_tokens", "reader_max_tokens"),
            ("include_captions", "include_captions"),
            ("source_input_policy", "source_input_policy"),
            ("input_request_byte_cap", "context_byte_limit")):
        if declared_key in declared and protocol.get(runtime_key) != declared[declared_key]:
            raise ValueError("Run mechanisms/order/budgets differ from registration")
    for name in ("cases.jsonl", "calls.jsonl"):
        if digest(run / name) != manifest["artifacts"][name]["sha256"]:
            raise ValueError("Frozen artifact fingerprint differs")
    arms, ids = declared.get("arm_order", declared["arms"]), declared["item_ids"]
    if (len(ids) != len(set(ids)) or len(ids) != declared["questions_per_arm"] or
            len(arms) != len(set(arms)) or set(arms) != set(declared["arms"])):
        raise ValueError("Invalid planned denominator")
    cases = read_cases(run, arms, ids)
    calls = list(lines(run / "calls.jsonl"))
    if any(row.get("arm") not in arms for row in calls):
        raise ValueError("Ledger contains undeclared arms")
    summaries = {}
    for arm in arms:
        rows = list(cases[arm].values())
        summary = summarize(arm, rows).to_dict()
        # Whitelist, rather than recursively sanitizing arbitrary experimental metadata.
        public = {key: summary[key] for key in (
            "items", "scored", "errors", "correct", "by_group", "primary", "latency_ms")}
        ordinary = [row for row in rows if row.group != "5"]
        public["outcomes"] = {
            "ordinary_answered": sum(r.answer_status == "answer" and not r.error for r in ordinary),
            "ordinary_wrong_answers": sum(r.answer_status == "answer" and not r.error and r.score == 0 for r in ordinary),
            "ordinary_incorrect_refusals": sum(r.answer_status in {"abstain", "clarify"} and not r.error and r.score == 0 for r in ordinary),
            "adversarial_overanswers": sum(r.group == "5" and r.answer_status == "answer" and not r.error for r in rows),
            "unscored_or_failed": sum(bool(r.error) or r.score is None for r in rows),
        }
        from memoria.experiments.eval_diagnostics import summarize_diagnostics
        diagnostics = summarize_diagnostics(rows)
        public["diagnostics"] = {key: diagnostics[key] for key in (
            "ordinary", "adversarial", "pipeline_stages", "gate_bindings", "answer_repairs",
            "gold_undefined_cases", "unavailable_gold_cases")}
        subset = [row for row in calls if row.get("arm") == arm]
        usage = cache_summary(subset)
        public["accounting"] = {"calls": len(subset), "usage": usage,
            "cost": cost_bounds(usage, declared["backbone"]),
            "unknown_reserved_upper_usd": sum(row["reserved_upper_usd"] for row in subset if row.get("usage") is None)}
        public["stages"] = {}
        for stage in ("formation", "answer_and_support", "independent_judge"):
            selected = [row for row in subset if row.get("stage") == stage]
            public["stages"][stage] = {"calls": len(selected),
                "cost": cost_bounds(cache_summary(selected), declared["backbone"])}
        summaries[arm] = public
    comparisons = {}
    historical_input = None
    for arm in arms:
        if arm != "memoria" and "memoria" in cases:
            comparisons[arm + "_to_memoria"] = paired(cases[arm], cases["memoria"])
    if previous:
        old_manifest = json.loads((Path(previous) / "manifest.json").read_text(encoding="utf-8"))
        if (old_manifest["status"] not in {"completed", "failed"} or
                digest(Path(previous) / "cases.jsonl") != old_manifest["artifacts"]["cases.jsonl"]["sha256"]):
            raise ValueError("Historical checkpoint is not frozen")
        if (old_manifest["protocol"]["dataset_sha256"] != protocol["dataset_sha256"] or
                old_manifest["protocol"]["judge_prompt_sha256"] != protocol["judge_prompt_sha256"] or
                old_manifest["protocol"]["model"] != protocol["model"]):
            raise ValueError("Historical dataset/judge differs")
        old_arms = old_manifest["protocol"].get("arms", ["memoria", "raw_context"])
        if (not isinstance(old_arms, list) or not old_arms or len(set(old_arms)) != len(old_arms)
                or "memoria" not in old_arms):
            raise ValueError("Historical run must declare its complete arm set including Memoria")
        # Read every declared historical arm and every planned ID, including failed
        # checkpoints. A previous Memoria-only run is a valid paired comparison.
        old = read_cases(previous, old_arms, ids)
        comparisons["historical_memoria_to_current"] = paired(old["memoria"], cases["memoria"])
        old_captions = bool(old_manifest["protocol"].get("include_captions", False))
        current_captions = bool(protocol.get("include_captions", False))
        historical_input = {"previous_includes_machine_captions": old_captions,
            "current_includes_machine_captions": current_captions,
            "same_caption_input_policy": old_captions == current_captions,
            "isolated_algorithm_effect": False}
    if primary_only:
        if "memoria" not in summaries:
            raise ValueError("Primary publication requires the Memoria arm")
        summaries = {"memoria": summaries["memoria"]}
        comparisons = {key: value for key, value in comparisons.items() if key == "historical_memoria_to_current"}
    return {"schema": "memoria-fixed-diagnostic-public-v1", "date": declared["date"],
        "protocol": declared["protocol"], "registration_sha256": digest(registration),
        "run_id": manifest["run_id"], "status": manifest["status"],
        "manifest_sha256": digest(run / "manifest.json"), "elapsed_ms": manifest["elapsed_ms"],
        "source_sha256": protocol["dataset_sha256"], "model": declared["backbone"],
        "judge_prompt_sha256": protocol["judge_prompt_sha256"],
        "code_commit": manifest["code"]["commit"], "dirty_diff_sha256": manifest["code"]["dirty_diff_sha256"],
        "arms": summaries, "paired_ordinary": comparisons,
        "input_policy": {"includes_separate_machine_captions": bool(protocol.get("include_captions", False)),
            "historical_comparison": historical_input},
        "publication_scope": "Memoria only; other arms are private mechanism diagnostics" if primary_only else "all registered arms",
        "entire_diagnostic_accounting": {"calls": len(calls),
            "cost": cost_bounds(cache_summary(calls), declared["backbone"])},
        "limits": ["Repeated development questions; no unseen generalization or new full-data claim.",
            "Planned denominators include all execution and grading failures.",
            "Incorrect refusals are judge-based, not independently annotated answerability.",
            "Evidence budget is complete JSON characters, not equal actual token/call cost.",
            "RRF-only removes the whole adaptive gate/support loop, not only the planner.",
            "Frozen-price estimates are not invoices; missing usage remains unknown."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--primary-only", action="store_true", help="Publish only Memoria; keep control scores private")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = publish(args.run, args.registration, previous=args.previous, primary_only=args.primary_only)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"run_id": result["run_id"], "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
