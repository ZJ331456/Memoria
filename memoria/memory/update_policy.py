"""Conservative shadow update policy; never commits or approves."""
from ..claims import SLOTS, canonical

def same(a, b):
    return canonical(a) == canonical(b)

def propose_update(item, snapshot, verification, pairs, support):
    peers = snapshot["peers"]
    targets = [{"id": p["id"], "revision": p["revision"], "revision_id": p["revision_id"]} for p in peers]
    report = {"version": "claim-update-policy-v2", "action": "review", "reason": "unresolved_relation", "targets": targets,
              "relations": pairs, "peer_count": len(peers), "truncated": snapshot["truncated"],
              "requires_explicit_approval": True, "automatic_mutations": 0, "semantic_support_verified": False}
    if verification["blockers"]:
        report["reason"] = "evidence_review_required:" + ";".join(verification["blockers"])
    elif support in {"Contradicted", "Insufficient"}:
        report["reason"] = "model_support_requires_review:" + support
    elif snapshot["truncated"]:
        report["reason"] = "peer_pool_truncated"
    elif item["status"] != "pending":
        report["reason"] = "only_pending_candidate_has_update_proposal"
    elif any(p["has_pending_revision"] for p in peers):
        report["reason"] = "target_has_pending_revision"
    else:
        exact = [p for p, pair in zip(peers, pairs) if pair["relation"] == "duplicate"]
        changes = [p for p, pair in zip(peers, pairs) if pair["relation"] == "state_change"]
        harmless = {"coexisting_value", "conditional_exception", "disjoint_scope", "historical_coexist"}
        if not peers or all(p["relation"] in harmless for p in pairs):
            report.update(action="create", reason="independent_or_additive_assertion; approval_required", targets=[])
        elif len(exact) == 1 and all(p["relation"] in harmless | {"duplicate"} for p in pairs):
            report.update(action="reinforce", reason="identical_typed_assertion; explicit_revision_required",
                          targets=[t for t in targets if t["id"] == exact[0]["id"]])
        elif len(changes) == 1 and all(p["relation"] in harmless | {"state_change"} for p in pairs):
            old, start = changes[0], item.get("fact_start")
            ready = (same(old["scope"], item["scope"]) and not SLOTS[item["predicate"]]
                     and old["modality"] == item["modality"] == "statement"
                     and old["polarity"] == item["polarity"] == "positive" and old["value"] != item["value"]
                     and verification["change_cue_present"] and start and verification["event_times_bound"]["fact_start"]
                     and (not old["fact_start"] or start > old["fact_start"])
                     and (not old["fact_end"] or start < old["fact_end"]))
            if ready:
                report.update(action="transition", reason="explicit_change_with_source_bound_date; approval_required",
                              targets=[t for t in targets if t["id"] == old["id"]])
            else:
                report["reason"] = "change_requires_bound_time_same_scope_and_single_valued_state"
    return report
