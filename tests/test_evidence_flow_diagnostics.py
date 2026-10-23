"""Visibility and support receipts never become semantic ground truth."""
from memoria.experiments.eval_benchmarks import BenchmarkItem
from memoria.experiments.eval_diagnostics import annotate_posthoc, inspect_kernel_result, summarize_diagnostics
from memoria.experiments.eval_schema import ArmResult


def test_gate_partial_binding_is_distinct_from_full_recall_and_reader_visibility():
    records = [{"citation": key, "provenance": {"source_unit": {"id": source}}}
        for key, source in (("a", "conv/D1:1"), ("b", "conv/D1:2"))]
    pipeline = inspect_kernel_result({"reader_called": False, "access": {"candidates": records,
        "adaptive": {"rounds": [{"omitted_from_gate": ["unassessed"], "decision": {
            "state": "missing_fact", "binding_integrity_verified": True, "requirements": [
                {"status": "supported", "sources": [{"id": "a"}]},
                {"status": "missing", "sources": []}]}}]}}})
    assert pipeline["gate"]["supported_requirements"] == 1
    assert not pipeline["gate"]["sufficient"] and not pipeline["gate"]["semantic_sufficiency_verified"]
    assert pipeline["stages"]["gate_bound_evidence"]["source_unit_ids"] == ["conv/D1:1"]
    assert pipeline["stages"]["reader_evidence"]["records"] == 0
    row = ArmResult("synthetic", "memoria", group="1", score=0, extras={"pipeline": pipeline})
    annotate_posthoc(row, BenchmarkItem("synthetic", "Q", "A", sources=[
        {"id": "conv/D1:1"}, {"id": "conv/D1:2"}], extras={"gold_evidence": ["D1:1; D1:2"]}))
    stages = row.extras["posthoc_gold"]["stages"]
    assert stages["final_candidates"]["all_gold_ids_present"]
    assert not stages["gate_bound_evidence"]["all_gold_ids_present"]
    assert not stages["reader_evidence"]["all_gold_ids_present"]
    summary = summarize_diagnostics([row])
    assert summary["gate_bindings"]["cases_with_unassessed_candidates"] == 1


def test_repair_counts_preserve_missing_question_coverage_without_promoting_a_score():
    rows = [ArmResult("synthetic", "memoria", group="1", score=None, error="provider", extras={
        "pipeline": {"answer_repair": {"attempted": True, "accepted": True,
            "question_requirement_coverage_verified": False}}})]
    result = summarize_diagnostics(rows)
    assert result["answer_repairs"] == {"attempted": 1, "accepted": 1,
        "accepted_with_all_question_requirements": 0}
    assert result["ordinary"]["scored"] == 0 and result["ordinary"]["errors_or_unknown"] == 1


def test_actual_reader_projection_and_published_provenance_override_routing_summaries():
    raw = {"citation": "a", "provenance": {"source_unit": {"id": "conv/D1:1"},
        "formed_memory": {"observations": [{"observation_id": "o", "summary": "Routing summary"}]}}}
    projected = {**raw, "provenance": {**raw["provenance"], "formed_memory": {
        "role": "retrieval_receipt_only", "observations": [{"observation_id": "o"}]}}}
    pipeline = inspect_kernel_result({"reader_called": True, "access": {"candidates": [raw],
        "evidence": [projected], "selection": {"payload": {"evidence": [raw]}}},
        "citations": [{"id": "a", "provenance": projected["provenance"]}]})
    stages = pipeline["stages"]
    assert stages["initial_candidates"]["visible_observation_summaries"] == 1
    for name in ("reader_evidence", "published_citations"):
        assert stages[name]["source_unit_ids"] == ["conv/D1:1"]
        assert stages[name]["observation_ids"] == 1
        assert stages[name]["visible_observation_summaries"] == 0
