"""Post-answer pipeline attribution. Source IDs are locators, never sufficiency labels.

No function in this module is called to choose evidence or produce an answer. In
particular, gold evidence is joined only after the system returns its final result.
"""
from __future__ import annotations

from collections import Counter
import re


def _records(candidates):
    for candidate in candidates or []:
        data = candidate.get("data", candidate)
        yield from data if isinstance(data, list) else [data]


def _key(record):
    return record.get("citation") or record.get("id")


def gold_locator_ids(values):
    """Normalize compound LoCoMo cells without dropping unknown locator formats.

    This runs only in post-answer attribution. Some official cells contain several
    IDs in one string, e.g. ``D1:2; D3:4``; treating the cell as a single locator
    incorrectly reports otherwise visible evidence as unavailable.
    """
    result = set()
    for value in values:
        raw = str(value).strip()
        normalized = raw.rsplit("/", 1)[-1].strip()
        cells = re.split(r"\s*[,;]\s*|\s+(?=(?:[^\s,;]+/)?D\d+:\d+\b)", raw)
        parts = [cell.rsplit("/", 1)[-1].strip() for cell in cells]
        if parts and all(re.fullmatch(r"D\d+:\d+", part) for part in parts):
            result.update(parts)
        else:
            result.add(normalized)
    return result


def _stage(records, source_index):
    records = list(records)
    source_ids, consolidated, observations, summaries = set(), set(), set(), set()
    hint_records = 0
    for record in records:
        provenance = record.get("provenance", {})
        if unit := provenance.get("source_unit"):
            source_ids.add(unit["id"])
        # A consolidated candidate carries raw support spans, not merely a group ID.
        for support in provenance.get("support", []):
            if sid := support.get("source_unit", {}).get("id"):
                source_ids.add(sid)
            revision, start, end = support.get("revision_id"), support.get("start", -1), support.get("end", -1)
            if match := re.fullmatch(r"\[library:([^:]+):(\d+):(\d+)\]", str(support.get("citation", ""))):
                revision, start, end = match[1], int(match[2]), int(match[3])
            for unit in source_index.get(revision, []):
                if unit["body_start"] <= start < end <= unit["end"]:
                    source_ids.add(unit["id"])
        if record.get("representation") == "consolidated_memory" or str(_key(record)).startswith("[consolidation:"):
            consolidated.add(_key(record))
        hints = provenance.get("formed_memory", {}).get("observations", [])
        if hints:
            hint_records += 1
        for hint in hints:
            if oid := hint.get("observation_id"):
                observations.add(oid)
                if hint.get("summary"):
                    summaries.add(oid)
    return {"records": len(records), "source_unit_ids": sorted(source_ids),
        "consolidated_summaries": len(consolidated), "observation_hint_records": hint_records,
        "observation_ids": len(observations), "visible_observation_summaries": len(summaries)}


def inspect_kernel_result(result, source_index=None):
    """Separate recall hints, Reader visibility and published consolidated citations."""
    source_index = source_index or {}
    access = result.get("access", {})
    adaptive = access.get("adaptive", {})
    initial = list(_records(adaptive.get("initial_candidates", access.get("candidates", []))))
    final = list(_records(access.get("candidates", [])))
    # Preserve candidates visible in supplement snapshots even after final ordering.
    by_key = {_key(record): record for record in final}
    for supplement in adaptive.get("supplements", []):
        for record in _records(supplement.get("candidates", [])):
            by_key.setdefault(_key(record), record)
    final = list(by_key.values())
    selected = list(_records(access.get("selection", {}).get("payload", {}).get("evidence", [])))
    # Source selection retains the original routing candidates for diagnosis;
    # access.evidence is the actual projected Reader packet (summaries removed).
    presented = access.get("evidence")
    reader = (presented if isinstance(presented, list) else selected) if result.get("reader_called") else []
    catalog = {_key(record): record for record in final + selected}
    cited = [{**catalog.get(c.get("id") or c.get("citation"), {}), **c}
        for c in result.get("citations", [])]
    rounds = adaptive.get("rounds", [])
    last = rounds[-1] if rounds else {}
    decision = last.get("decision", {})
    requirements = decision.get("requirements", [])
    bound_ids = {s["id"] for r in requirements if r["status"] == "supported" for s in r["sources"]}
    bound = [catalog[eid] for eid in sorted(bound_ids) if eid in catalog]
    repair = adaptive.get("answer_repair", {})
    return {"scope": "post-answer ID/representation visibility; not semantic necessity or causal benefit",
        "reader_called": bool(result.get("reader_called")),
        "stages": {"initial_candidates": _stage(initial, source_index),
            "final_candidates": _stage(final, source_index),
            "gate_bound_evidence": _stage(bound, source_index), "reader_evidence": _stage(reader, source_index),
            "published_citations": _stage(cited, source_index)},
        "gate": {"rounds": len(rounds), "requirements": len(requirements),
            "supported_requirements": sum(r["status"] == "supported" for r in requirements),
            "binding_integrity_verified": bool(decision.get("binding_integrity_verified")),
            "sufficient": decision.get("state") == "sufficient",
            "unassessed_candidates": len(last.get("omitted_from_gate", [])),
            "semantic_sufficiency_verified": False},
        "answer_repair": {"attempted": bool(repair.get("attempted")),
            "accepted": bool(repair.get("accepted")),
            "retained_units": int(repair.get("retained_unit_count", 0)),
            "question_requirement_coverage_verified": bool(adaptive.get("answer_support_after_repair", {}).get(
                "question_requirement_coverage_verified"))},
        "citation_interpretation": "Raw citations with observation hints are raw-source citations; only consolidated_memory is an explicit formed-memory citation"}


def annotate_posthoc(result, item):
    """Join official gold IDs only after answering; unknown/missing IDs remain explicit."""
    pipeline = result.extras.get("pipeline")
    if pipeline is None and result.arm == "raw_context" and result.answer_status:
        ids = sorted(s["id"] for s in result.retrieved)
        stage = {"records": len(ids), "source_unit_ids": ids, "consolidated_summaries": 0,
            "observation_hint_records": 0, "observation_ids": 0, "visible_observation_summaries": 0}
        cited = [s["id"] for s in result.retrieved if s["id"] in result.evidence_ids]
        pipeline = {"reader_called": True, "stages": {"initial_candidates": stage,
            "final_candidates": stage, "reader_evidence": stage,
            "published_citations": {**stage, "records": len(cited), "source_unit_ids": cited}}}
        result.extras["pipeline"] = pipeline
    normalize = lambda value: str(value).rsplit("/", 1)[-1]
    gold = gold_locator_ids(item.extras.get("gold_evidence", []))
    visible = {normalize(source["id"]) for source in item.sources}
    result.extras["posthoc_gold"] = {"scope": "official locator ID coverage, not semantic sufficiency",
        "normalization_version": "compound-locators-v2",
        "gold_defined": bool(gold), "gold_ids": sorted(gold), "unavailable_gold_ids": sorted(gold-visible),
        "all_gold_visible": bool(gold) and gold <= visible,
        "stages": {name: {"matched_ids": sorted(gold & {normalize(value) for value in stage["source_unit_ids"]}),
            "all_gold_ids_present": bool(gold) and gold <= {normalize(value) for value in stage["source_unit_ids"]}}
            for name, stage in (pipeline or {}).get("stages", {}).items()}}


def summarize_diagnostics(results):
    """Judge-based errors stay distinct from refusal/answer actions and technical errors."""
    ordinary = [r for r in results if r.group in {"1", "2", "3", "4"}]
    adversarial = [r for r in results if r.group == "5"]

    def actions(rows):
        valid = [r for r in rows if not r.error and r.score is not None]
        return {"planned": len(rows), "scored": len(valid), "errors_or_unknown": len(rows)-len(valid),
            "answer_actions": sum(r.answer_status == "answer" for r in valid),
            "refusal_actions": sum(r.answer_status == "abstain" for r in valid),
            "clarify_actions": sum(r.answer_status == "clarify" for r in valid),
            "wrong_answers": sum(r.answer_status == "answer" and r.score == 0 for r in valid),
            "wrong_refusals": sum(r.answer_status == "abstain" and r.score == 0 for r in valid),
            "wrong_clarifications": sum(r.answer_status == "clarify" and r.score == 0 for r in valid)}

    stages = {}
    for name in ("initial_candidates", "final_candidates", "gate_bound_evidence", "reader_evidence", "published_citations"):
        rows = [r.extras["pipeline"]["stages"][name] for r in results
            if name in r.extras.get("pipeline", {}).get("stages", {})]
        gold = [r.extras["posthoc_gold"]["stages"][name] for r in results
            if r.extras.get("posthoc_gold", {}).get("all_gold_visible")
            and name in r.extras["posthoc_gold"]["stages"]]
        eligible = sum(bool(r.extras.get("posthoc_gold", {}).get("all_gold_visible")) for r in results)
        stages[name] = {"observed_cases": len(rows),
            "cases_with_consolidated_summaries": sum(bool(r["consolidated_summaries"]) for r in rows),
            "cases_with_observation_hints": sum(bool(r["observation_ids"]) for r in rows),
            "cases_with_visible_observation_summaries": sum(bool(r["visible_observation_summaries"]) for r in rows),
            "all_gold_visible_eligible_cases": eligible,
            "all_gold_visible_observed_cases": len(gold),
            "all_gold_visible_unknown_stage_cases": eligible-len(gold),
            "all_gold_ids_present_cases": sum(r["all_gold_ids_present"] for r in gold)}
    adversarial_actions = actions(adversarial)
    adversarial_actions.update(overanswer_actions=adversarial_actions["answer_actions"],
        wrong_overanswers=adversarial_actions["wrong_answers"])
    repairs = [r.extras.get("pipeline", {}).get("answer_repair", {}) for r in results]
    gates = [r.extras.get("pipeline", {}).get("gate", {}) for r in results]
    return {"ordinary": actions(ordinary), "adversarial": adversarial_actions,
        "gate_bindings": {"observed_cases": sum(bool(g) for g in gates),
            "sufficient_cases": sum(bool(g.get("sufficient")) for g in gates),
            "cases_with_unassessed_candidates": sum(bool(g.get("unassessed_candidates")) for g in gates),
            "semantic_sufficiency_verified": False},
        "answer_repairs": {"attempted": sum(bool(r.get("attempted")) for r in repairs),
            "accepted": sum(bool(r.get("accepted")) for r in repairs),
            "accepted_with_all_question_requirements": sum(bool(r.get("accepted")) and bool(
                r.get("question_requirement_coverage_verified")) for r in repairs)},
        "technical_errors": dict(Counter(r.error for r in results if r.error)), "pipeline_stages": stages,
        "gold_undefined_cases": sum(not r.extras.get("posthoc_gold", {}).get("gold_defined") for r in results),
        "unavailable_gold_cases": sum(bool(r.extras.get("posthoc_gold", {}).get("unavailable_gold_ids")) for r in results),
        "interpretation": "Judge-based error labels; actions exclude execution/judge failures. Hint/summary visibility and gold IDs do not establish semantic use."}


def paired_changes(baseline, treatment):
    """Pair by immutable IDs; unknown pairs never turn into wins, losses or dropped cases."""
    left, right = {r.id: r for r in baseline}, {r.id: r for r in treatment}
    if len(left) != len(baseline) or len(right) != len(treatment):
        raise ValueError("Duplicate evaluation item IDs")
    if left.keys() != right.keys():
        return {"same_denominator": False, "note": "different item IDs; no paired delta"}
    if any(left[key].group != right[key].group for key in left):
        raise ValueError("Paired category labels differ")

    def counts(ids):
        result = {"planned_pairs": len(ids), "both_correct": 0, "both_wrong": 0,
            "improved": 0, "regressed": 0, "unknown_pairs": 0}
        for key in ids:
            a, b = left[key], right[key]
            if a.error or b.error or a.score is None or b.score is None:
                label = "unknown_pairs"
            elif a.score == b.score:
                label = "both_correct" if a.score == 1 else "both_wrong"
            else:
                label = "improved" if b.score == 1 else "regressed"
            result[label] += 1
        result["observed_net_improvements"] = result["improved"]-result["regressed"]
        result["accuracy_delta"] = result["observed_net_improvements"]/len(ids) if ids and not result["unknown_pairs"] else None
        return result

    return {"same_denominator": True, "all": counts(list(left)),
        "primary": counts([key for key, r in left.items() if r.group in {"1", "2", "3", "4"}]),
        "adversarial": counts([key for key, r in left.items() if r.group == "5"]),
        "by_group": {group: counts([key for key, r in left.items() if r.group == group])
            for group in sorted({r.group for r in baseline})}}
