"""Registered ablations and post-answer attribution; synthetic fixtures, zero paid calls."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from memoria.config import Settings
from memoria.experiments.eval_benchmarks import BenchmarkItem
from memoria.experiments.eval_command import ARM_CONFIGURATION, registered_arm_order
from memoria.experiments.eval_diagnostics import (
    annotate_posthoc, inspect_kernel_result, paired_changes, summarize_diagnostics,
)
from memoria.experiments.eval_runner import build_report, run_arms
from memoria.experiments.eval_schema import ArmResult
from memoria.experiments.eval_systems import KernelMemorySystem


def registration():
    return {"arms": list(ARM_CONFIGURATION), "arm_order": list(ARM_CONFIGURATION),
        "arm_configuration": copy.deepcopy(ARM_CONFIGURATION)}


def test_registered_arms_order_and_mechanisms_cannot_drift():
    record = registration()
    assert registered_arm_order(list(ARM_CONFIGURATION), record) == record["arm_order"]
    for field, value in (("arms", ["memoria", "raw_context"]),
                         ("arm_order", ["memoria"]), ("arm_configuration", {})):
        invalid = {**record, field: value}
        with pytest.raises(ValueError):
            registered_arm_order(list(ARM_CONFIGURATION), invalid)
    record["arm_configuration"]["source_only"]["include_formed_memory"] = True
    with pytest.raises(ValueError, match="mechanisms"):
        registered_arm_order(list(ARM_CONFIGURATION), record)
    assert registered_arm_order(["memoria", "raw_context"], {}) == ["memoria", "raw_context"]


@pytest.mark.parametrize("name,adaptive", [("source_only", True), ("rrf_only", False)])
def test_raw_source_ablation_uses_native_reader_without_forming_or_exposing_memories(tmp_path, name, adaptive):
    calls, ingested = [], []
    def ingest(source):
        ingested.append(source)
        return {"id": "source", "head_id": "revision"}
    async def form(*args):
        pytest.fail("Source-only arm must never send paid formation requests")
    async def answer(request):
        calls.append(request)
        return {"status": "abstain", "answer": "Not mentioned.", "reader_called": False}
    kernel = SimpleNamespace(store=SimpleNamespace(create_session=lambda *args: {"id": "session"},
        close=lambda: None), ingest=ingest, answer=answer,
        library=SimpleNamespace(formation=SimpleNamespace(form=form)))
    system = KernelMemorySystem(lambda _: kernel, directory=tmp_path, name=name,
        form_memory=False, include_formed_memory=False, adaptive=adaptive)
    source = {"id": "conversation/D1:1", "text": "An original quote", "speaker": "A", "session": 1}
    async def run():
        system.reset([source])
        await system.ingest(source)
        await system.drain()
        await system.drain()
        return await system.answer("What?")
    result = asyncio.run(run())
    assert len(ingested) == 1 and ingested[0].authorship == "external"
    assert calls[0].budget_chars == 8000
    assert calls[0].include_formed_memory is False
    assert calls[0].use_adaptive_retrieval is adaptive
    assert result["extras"]["formation_receipts"] == []
    assert result["extras"]["pipeline"]["reader_called"] is False


def test_formation_error_stays_in_every_planned_case_without_retrying_the_batch(tmp_path):
    attempts = []
    async def fail(*args):
        attempts.append(1)
        raise RuntimeError("engineering-only failure")
    kernel = SimpleNamespace(store=SimpleNamespace(create_session=lambda *args: {"id": "s"}, close=lambda: None),
        ingest=lambda source: {"id": "source"}, library=SimpleNamespace(formation=SimpleNamespace(form=fail)))
    system = KernelMemorySystem(lambda _: kernel, directory=tmp_path)
    sources = [{"id": "D1", "text": "Original", "session": 1}]
    items = [BenchmarkItem(str(i), "Q", "A", sources=sources) for i in range(3)]
    rows = asyncio.run(run_arms({"memoria": system}, items))["memoria"]
    assert len(rows) == 3 and len(attempts) == 1
    assert all(row.error and row.score is None for row in rows)


def test_nonadaptive_invalid_citations_cannot_score_as_correct_adversarial_refusal(tmp_path):
    async def answer(request):
        return {"status": "abstain", "answer": "Not mentioned.", "reader_called": True,
            "validation": {"valid": False, "reason": "unseen_or_missing_source_citation"}}
    kernel = SimpleNamespace(store=SimpleNamespace(create_session=lambda *args: {"id": "s"}, close=lambda: None),
        answer=answer)
    system = KernelMemorySystem(lambda _: kernel, name="rrf_only", directory=tmp_path,
        form_memory=False, include_formed_memory=False, adaptive=False)
    class Judge:
        async def grade(self, *args):
            pytest.fail("Technical citation errors must remain ungraded")
    row = asyncio.run(run_arms({"rrf_only": system},
        [BenchmarkItem("q", "Q", "Not mentioned.", group="5")], judge=Judge()))["rrf_only"][0]
    assert row.score is None and row.error == "kernel_execution_failure:unseen_or_missing_source_citation"


def raw_record(id="raw", observation_id="obs", summary="A reviewed summary"):
    return {"citation": id, "representation": "source_quote", "provenance": {
        "source_unit": {"id": "conv/D1:1"}, "formed_memory": {"role": "retrieval_organization_only",
            "observations": [{"observation_id": observation_id, "summary": summary}]}}}


def test_hints_reader_visibility_and_explicit_consolidation_citations_are_separate():
    raw = raw_record()
    hidden = raw_record("other", "hidden", "")
    formed = {"citation": "[consolidation:v]", "representation": "consolidated_memory",
        "provenance": {"support": [{"source_unit": {"id": "conv/D2:2"}}]}}
    result = {"reader_called": True, "access": {"candidates": [raw, hidden, formed],
        "selection": {"payload": {"evidence": [raw, formed]}}}, "citations": [{"id": "raw"}]}
    stages = inspect_kernel_result(result)["stages"]
    assert stages["initial_candidates"]["observation_ids"] == 2
    assert stages["initial_candidates"]["visible_observation_summaries"] == 1
    assert stages["reader_evidence"]["consolidated_summaries"] == 1
    assert stages["reader_evidence"]["source_unit_ids"] == ["conv/D1:1", "conv/D2:2"]
    assert stages["published_citations"]["consolidated_summaries"] == 0
    assert stages["published_citations"]["visible_observation_summaries"] == 1
    result["reader_called"] = False
    assert inspect_kernel_result(result)["stages"]["reader_evidence"]["records"] == 0


def test_gold_join_happens_only_after_answer_and_unavailable_ids_stay_unknown():
    seen = []
    class System:
        async def ingest(self, source):
            assert "gold" not in str(source)
        async def answer(self, question):
            seen.append(question)
            return {"answer": '{"status":"answer","answer":"A","evidence_ids":["conv/D1:1"]}',
                "retrieved": [{"id": "conv/D1:1"}]}
    item = BenchmarkItem("q", "Q", "SECRET GOLD ANSWER", group="1",
        sources=[{"id": "conv/D1:1", "text": "A"}], extras={"gold_evidence": ["D1:1", "D9:9"]})
    row = asyncio.run(run_arms({"raw_context": System()}, [item]))["raw_context"][0]
    assert seen == ["Q"]
    assert row.extras["posthoc_gold"]["unavailable_gold_ids"] == ["D9:9"]
    assert row.extras["posthoc_gold"]["all_gold_visible"] is False
    assert row.extras["posthoc_gold"]["stages"]["reader_evidence"]["matched_ids"] == ["D1:1"]


def test_technical_errors_do_not_become_wrong_refusals_or_correct_adversarial_abstentions():
    rows = [ArmResult("a", "x", group="1", score=0, answer_status="abstain"),
        ArmResult("b", "x", group="1", score=0, answer_status="answer"),
        ArmResult("c", "x", group="1", error="provider", answer_status="abstain"),
        ArmResult("d", "x", group="5", score=0, answer_status="answer"),
        ArmResult("e", "x", group="5", error="budget", answer_status="answer")]
    diagnostics = summarize_diagnostics(rows)
    assert diagnostics["ordinary"]["planned"] == 3
    assert diagnostics["ordinary"]["wrong_refusals"] == 1
    assert diagnostics["ordinary"]["wrong_answers"] == 1
    assert diagnostics["ordinary"]["errors_or_unknown"] == 1
    assert diagnostics["adversarial"]["overanswer_actions"] == 1
    assert diagnostics["adversarial"]["wrong_overanswers"] == 1
    assert diagnostics["adversarial"]["errors_or_unknown"] == 1


def test_paired_changes_keep_unknowns_and_group_denominators():
    before = [ArmResult("a", "old", group="1", score=0), ArmResult("b", "old", group="1", score=1),
        ArmResult("c", "old", group="5", error="unknown")]
    after = [ArmResult("c", "new", group="5", score=1), ArmResult("b", "new", group="1", score=0),
        ArmResult("a", "new", group="1", score=1)]
    delta = paired_changes(before, after)
    assert delta["primary"]["improved"] == delta["primary"]["regressed"] == 1
    assert delta["primary"]["accuracy_delta"] == 0
    assert delta["all"]["unknown_pairs"] == 1 and delta["all"]["accuracy_delta"] is None
    assert delta["adversarial"]["planned_pairs"] == 1
    assert paired_changes(before, after[:2])["same_denominator"] is False


def test_report_includes_direct_formed_memory_ablation_and_fixed_summary_diagnostics(tmp_path):
    results = {name: [ArmResult("q", name, group="1", score=1, answer_status="answer")]
        for name in ARM_CONFIGURATION}
    report = build_report(tmp_path, benchmark="locomo", metadata={}, results_by_arm=results)
    comparisons = {(r["baseline_arm"], r["treatment_arm"]): r for r in report["comparisons"]}
    assert ("source_only", "memoria") in comparisons
    assert ("rrf_only", "source_only") in comparisons
    assert comparisons[("source_only", "memoria")]["paired_changes"]["all"]["both_correct"] == 1
    assert report["summaries"][0]["diagnostics"]["ordinary"]["planned"] == 1


def test_four_arm_dry_estimate_includes_every_judge_and_only_one_formation(tmp_path):
    from memoria.experiments.eval_estimate import estimate
    dataset = tmp_path / "input.json"
    dataset.write_text("[]", encoding="utf-8")
    items = [BenchmarkItem("q", "Q", "A", sources=[{"id": "D1", "text": "A"}])]
    plan = estimate(items, dataset, model="deepseek-flash", arms=list(ARM_CONFIGURATION))
    for scenario in plan["scenarios"]:
        phases = scenario["phases"]
        assert phases["independent_judge"]["calls"] == 4
        assert {k for k in phases if "formation" in k} == {"kernel_formation"}
        assert "source_only_answers_and_checks" in phases and "rrf_only_answers" in phases
