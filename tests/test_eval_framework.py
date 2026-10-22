"""Unified evaluation framework: schema, adapters, arms, judge and the baseline comparison.

Synthetic inputs only. No paid requests and no account writes.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from memoria.experiments.eval_benchmarks import load_items, parse_session_date
from memoria.experiments.eval_judge import is_refusal, parse_judgment
from memoria.experiments.eval_runner import build_report, parse_answer_payload, run_arms
from memoria.experiments.eval_schema import compare, summarize


def test_summarize_preserves_failure_denominator():
    from memoria.experiments.eval_schema import ArmResult

    results = [
        ArmResult(id="a", arm="x", score=1.0),
        ArmResult(id="b", arm="x", score=0.0),
        ArmResult(id="c", arm="x", error="boom"),
    ]
    summary = summarize("x", results)
    assert summary.items == 3
    assert summary.scored == 2
    assert summary.errors == 1
    assert summary.accuracy == pytest.approx(1 / 3)
    assert summary.by_group["all"]["items"] == 3


def test_compare_refuses_a_delta_when_denominators_differ():
    from memoria.experiments.eval_schema import ArmResult

    baseline = summarize("raw_context", [ArmResult(id="a", arm="raw_context", score=1.0)])
    treatment = summarize("memoria", [ArmResult(id="a", arm="memoria", score=1.0),
                                      ArmResult(id="b", arm="memoria", score=1.0)])
    result = compare(baseline, treatment)
    assert result["same_denominator"] is False
    assert result["accuracy_delta"] is None
    assert "no delta" in result["note"]


def test_compare_states_the_delta_for_equal_denominators():
    from memoria.experiments.eval_schema import ArmResult

    baseline = summarize("raw_context", [ArmResult(id="a", arm="raw_context", score=0.0)])
    treatment = summarize("memoria", [ArmResult(id="a", arm="memoria", score=1.0)])
    result = compare(baseline, treatment)
    assert result["same_denominator"] is True
    assert result["accuracy_delta"] == 1.0


def test_parse_answer_payload_rejects_a_non_json_reply_without_inventing_an_answer():
    assert parse_answer_payload("")["status"] == "invalid"
    assert parse_answer_payload("no braces here")["status"] == "invalid"
    parsed = parse_answer_payload('```json\n{"status":"answer","answer":"x","evidence_ids":["a"]}\n```')
    assert parsed["status"] == "answer"
    assert parsed["answer"] == "x"
    assert parsed["evidence_ids"] == ["a"]


def test_judgment_parsing_never_turns_an_unreadable_reply_into_a_wrong_answer():
    assert parse_judgment("")["label"] == "UNKNOWN"
    assert parse_judgment("maybe")["label"] == "UNKNOWN"
    assert parse_judgment('{"label":"correct"}')["label"] == "CORRECT"
    assert parse_judgment('{"label":"WRONG","reasoning":"x"}')["reason"] == "x"


def test_refusal_detection_covers_the_forms_the_system_actually_produces():
    assert is_refusal("Not mentioned.") is True
    assert is_refusal("No information available") is True
    assert is_refusal("目前缺少可核验的适用记忆") is True
    assert is_refusal("7 May 2023") is False


def test_parse_session_date_keeps_an_unparsable_date_unknown():
    assert parse_session_date("1:56 pm on 8 May, 2023").startswith("2023-05-08")
    assert parse_session_date("") is None
    assert parse_session_date("not a date") is None


def test_equal_counts_with_different_ids_are_not_comparable():
    from memoria.experiments.eval_schema import ArmResult
    a = summarize("raw_context", [ArmResult(id="a", arm="raw_context", score=1)])
    b = summarize("memoria", [ArmResult(id="b", arm="memoria", score=1)])
    assert compare(a, b)["accuracy_delta"] is None


def test_locomo_adapter_keeps_every_turn_visible_and_the_gold_out_of_the_input(tmp_path):
    dataset = [
        {
            "sample_id": "conv-test",
            "conversation": {
                "speaker_a": "A",
                "speaker_b": "B",
                "session_1_date_time": "1:56 pm on 8 May, 2023",
                "session_1": [
                    {"dia_id": "D1:1", "speaker": "A", "text": "First turn."},
                    {"dia_id": "D1:2", "speaker": "B", "text": "Second turn."},
                ],
                "session_2_date_time": "2:00 pm on 9 May, 2023",
                "session_2": [
                    {"dia_id": "D2:1", "speaker": "A", "text": "Third turn."},
                ],
            },
            "qa": [
                {"question": "When?", "answer": "8 May 2023", "evidence": ["D1:1"], "category": 2},
                {"question": "What?", "answer": "Third turn", "evidence": ["D2:1"], "category": 3},
            ],
        }
    ]
    path = tmp_path / "locomo.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")
    items = load_items("locomo", path, conversations=1)
    assert len(items) == 2
    # Every question is asked after the whole conversation, so all turns stay visible.
    for item in items:
        assert [s["id"] for s in item.sources] == [
            "conv-test/D1:1",
            "conv-test/D1:2",
            "conv-test/D2:1",
        ]
    # The gold evidence is carried for scoring only and never trims the input.
    assert items[0].extras["gold_evidence"] == ["D1:1"]
    assert items[0].sources[0]["session_date_raw"] == "1:56 pm on 8 May, 2023"
    assert items[0].group == "2"


def test_unknown_benchmark_fails_loudly(tmp_path):
    path = tmp_path / "x.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported benchmark"):
        load_items("nope", path)


class _Echo:
    """A fake memory system that answers with a fixed string."""

    name = "echo"

    def __init__(self, text="7 May 2023"):
        self.text = text
        self.seen = []

    def reset(self, sources=None):
        self.seen = []

    async def ingest(self, source):
        self.seen.append(source)

    async def answer(self, question):
        return {"answer": json.dumps({"status": "answer", "answer": self.text,
                                     "evidence_ids": []}), "usage": {"total_tokens": 12}}


class _Failing:
    name = "failing"

    async def ingest(self, source):
        return None

    async def answer(self, question):
        raise RuntimeError("no answer")


def test_run_arms_reports_a_failure_instead_of_dropping_the_item():
    from memoria.experiments.eval_benchmarks import BenchmarkItem

    items = [BenchmarkItem(id="q1", question="When?", ground_truth="7 May 2023", group="2")]
    results = asyncio.run(run_arms({"memoria": _Failing()}, items, benchmark="locomo"))
    assert results["memoria"][0].error is not None
    assert "RuntimeError" in results["memoria"][0].error
    assert results["memoria"][0].score is None


def test_run_arms_gives_every_arm_the_same_items():
    from memoria.experiments.eval_benchmarks import BenchmarkItem

    items = [
        BenchmarkItem(id="q1", question="When?", ground_truth="7 May 2023", group="2",
                      sources=[{"id": "s1", "text": "a"}]),
        BenchmarkItem(id="q2", question="Who?", ground_truth="B", group="1",
                      sources=[{"id": "s2", "text": "b"}]),
    ]
    results = asyncio.run(
        run_arms({"raw_context": _Echo(), "memoria": _Echo()}, items, benchmark="locomo")
    )
    assert [r.id for r in results["raw_context"]] == ["q1", "q2"]
    assert [r.id for r in results["memoria"]] == ["q1", "q2"]


def test_run_arms_resets_each_arm_so_a_later_question_cannot_see_earlier_sources():
    from memoria.experiments.eval_benchmarks import BenchmarkItem

    items = [
        BenchmarkItem(id="q1", question="When?", ground_truth="a", group="2",
                      sources=[{"id": "s1", "text": "a"}]),
        BenchmarkItem(id="q2", question="Who?", ground_truth="b", group="1",
                      sources=[{"id": "s2", "text": "b"}]),
    ]

    class _Counting:
        name = "counting"

        def __init__(self):
            self.per_question = []

        def reset(self, sources=None):
            self.per_question.append([])

        async def ingest(self, source):
            self.per_question[-1].append(source["id"])

        async def answer(self, question):
            return {"answer": "x"}

    arm = _Counting()
    asyncio.run(run_arms({"memoria": arm}, items, benchmark="locomo"))
    # Each question starts from an empty system and only sees its own sources.
    assert arm.per_question == [["s1"], ["s2"]]


def test_kernel_arm_rebuilds_only_when_the_visible_history_changes():
    from memoria.experiments.eval_systems import KernelMemorySystem

    built = []

    class _Store:
        def close(self):
            pass
        def create_session(self, title):
            return {"id": "session"}

    class _Kernel:
        store = _Store()

    def factory(database):
        built.append(database)
        return _Kernel()

    arm = KernelMemorySystem(factory)
    arm.reset([{"id": "s1"}])
    first = arm.kernel
    arm.reset([{"id": "s1"}])
    assert arm.kernel is first
    assert len(built) == 1
    arm.reset([{"id": "s1"}, {"id": "s2"}])
    assert arm.kernel is not first
    assert len(built) == 2


def test_build_report_writes_the_unified_files(tmp_path):
    from memoria.experiments.eval_benchmarks import BenchmarkItem

    items = [BenchmarkItem(id="q1", question="When?", ground_truth="7 May 2023", group="2")]
    results = asyncio.run(run_arms({"raw_context": _Echo(), "memoria": _Echo()}, items,
                                   benchmark="locomo"))
    target = tmp_path / "out"
    payload = build_report(target, benchmark="locomo", metadata={"test": True},
                           results_by_arm=results)
    assert (target / "summary.json").is_file()
    assert (target / "results.jsonl").is_file()
    assert (target / "report.json").is_file()
    names = {summary["arm"] for summary in payload["summaries"]}
    assert names == {"raw_context", "memoria"}
    comparisons = {entry["treatment_arm"]: entry for entry in payload["comparisons"]}
    assert "memoria" in comparisons
    assert comparisons["memoria"]["same_denominator"] is True


def test_raw_context_arm_truncates_the_oldest_turns_and_reports_the_drop():
    from memoria.experiments.eval_systems import RawContextSystem
    from memoria.config import Settings
    from memoria.prompting.reader_budget import ReaderBudget

    class _Client:
        settings = Settings.load(Path(__file__).resolve().parents[1] / "config/system.example.toml")
        reader_budget = ReaderBudget(settings)

        async def chat(self, messages, **kwargs):
            self.messages = messages
            return type("R", (), {"content": "{}", "usage": None})()

    arm = RawContextSystem(_Client(), system="s", budget_chars=600)
    for key in ["old", "middle", "new"]:
        asyncio.run(arm.ingest({"id": key, "text": "x" * 200, "speaker": "A",
                               "session_date_raw": "8 May 2023"}))
    result = asyncio.run(arm.answer("When?"))
    assert result["extras"]["dropped_turns"] == 2
    assert result["extras"]["visible_turns"] == 1
    assert result["extras"]["first_retained_id"] == "new"
