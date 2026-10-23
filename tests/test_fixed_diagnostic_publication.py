"""Publication must neither leak private fields nor silently shrink denominators."""
import json

import pytest

from scripts.publish_fixed36 import digest, publish


def fixture_run(tmp_path):
    secret = "PRIVATE_QUESTION_ANSWER_AND_SOURCE"
    registration = {"protocol": "fixed-test", "date": "2026-10-23",
        "item_ids": ["a", "b"], "arms": ["memoria", "raw_context"],
        "questions_per_arm": 2, "source_sha256": "source", "backbone": "deepseek-flash",
        "judge_prompt_sha256": "judge"}
    reg = tmp_path / "registration.json"
    reg.write_text(json.dumps(registration), encoding="utf-8")
    rows = [{"id": ident, "arm": arm, "group": "4", "score": None if ident == "b" else 1,
             "correct": None if ident == "b" else True,
             "answer_status": "answer", "question": secret, "answer": secret,
             "ground_truth": secret, "error": secret if ident == "b" else None,
             "extras": {"trace": secret}}
            for arm in registration["arms"] for ident in registration["item_ids"]]
    (tmp_path / "cases.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    call = {"arm": "memoria", "stage": "answer_and_support", "usage": None,
            "reserved_upper_usd": 0.1, "raw": secret, "messages": [secret]}
    (tmp_path / "calls.jsonl").write_text(json.dumps(call) + "\n", encoding="utf-8")
    manifest = {"status": "failed", "run_id": "test", "elapsed_ms": 12,
        "code": {"commit": "code", "dirty_diff_sha256": "diff"},
        "protocol": {**registration, "dataset_sha256": "source", "model": "deepseek-flash"},
        "artifacts": {name: {"sha256": digest(tmp_path / name)} for name in ("cases.jsonl", "calls.jsonl")}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return reg, secret


def test_export_whitelists_private_fields_and_preserves_failed_denominator(tmp_path):
    reg, secret = fixture_run(tmp_path)
    result = publish(tmp_path, reg)
    assert secret not in json.dumps(result)
    memory = result["arms"]["memoria"]
    assert memory["primary"]["items"] == 2
    assert memory["primary"]["accuracy"] == 0.5
    assert memory["errors"] == 1
    assert memory["accounting"]["unknown_reserved_upper_usd"] == 0.1
    assert result["paired_ordinary"]["raw_context_to_memoria"]["unknown"] == 1


def test_rejects_tampering_before_publication(tmp_path):
    reg, _ = fixture_run(tmp_path)
    with (tmp_path / "cases.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{}\n")
    with pytest.raises(ValueError, match="fingerprint"):
        publish(tmp_path, reg)


def test_rejects_missing_planned_checkpoint_even_with_updated_hash(tmp_path):
    reg, _ = fixture_run(tmp_path)
    path = tmp_path / "cases.jsonl"
    path.write_text("\n".join(path.read_text(encoding="utf-8").splitlines()[:-1]) + "\n", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][path.name]["sha256"] = digest(path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="Every planned"):
        publish(tmp_path, reg)


def test_rejects_registered_mechanism_or_budget_drift(tmp_path):
    reg, _ = fixture_run(tmp_path)
    declared = json.loads(reg.read_text(encoding="utf-8"))
    declared["kernel_evidence_chars"] = 8000
    reg.write_text(json.dumps(declared), encoding="utf-8")
    with pytest.raises(ValueError, match="mechanisms/order/budgets"):
        publish(tmp_path, reg)


def test_primary_publication_exposes_one_framework_and_retains_all_diagnostic_cost(tmp_path):
    reg, _ = fixture_run(tmp_path)
    result = publish(tmp_path, reg, primary_only=True)
    assert set(result["arms"]) == {"memoria"}
    assert "raw_context" not in json.dumps(result)
    assert result["paired_ordinary"] == {}
    assert result["entire_diagnostic_accounting"]["calls"] == 1


def test_previous_single_framework_is_paired_without_dropping_its_failure(tmp_path):
    current = tmp_path / "current"
    previous = tmp_path / "previous"
    current.mkdir()
    previous.mkdir()
    reg, _ = fixture_run(current)
    fixture_run(previous)
    path = previous / "cases.jsonl"
    path.write_text("".join(line + "\n" for line in path.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["arm"] == "memoria"), encoding="utf-8")
    manifest_path = previous / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["protocol"]["arms"] = ["memoria"]
    manifest["artifacts"]["cases.jsonl"]["sha256"] = digest(path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = publish(current, reg, previous=previous, primary_only=True)
    paired = result["paired_ordinary"]["historical_memoria_to_current"]
    assert paired == {"both_correct": 1, "both_incorrect": 0,
        "improved": 0, "regressed": 0, "unknown": 1}
