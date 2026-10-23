"""Post-answer locator parsing; synthetic IDs, no model calls or score changes."""
from memoria.experiments.eval_benchmarks import BenchmarkItem
from memoria.experiments.eval_diagnostics import annotate_posthoc, gold_locator_ids
from memoria.experiments.eval_schema import ArmResult


def test_compound_official_cells_and_paths_are_split_and_deduplicated():
    assert gold_locator_ids(["conv/D1:2; conv/D3:4", "D3:4, D5:6", "D7:8 D9:10"]) == {
        "D1:2", "D3:4", "D5:6", "D7:8", "D9:10"}


def test_unknown_locator_formats_remain_unknown_instead_of_being_erased():
    assert gold_locator_ids(["conv/nonstandard", "D1:2; missing", "D1:2extra"]) == {
        "nonstandard", "D1:2; missing", "D1:2extra"}


def test_posthoc_compound_coverage_changes_only_diagnostics_not_frozen_score():
    row = ArmResult("synthetic", "memoria", group="1", score=0, correct=False,
        extras={"pipeline": {"stages": {"final_candidates": {
            "source_unit_ids": ["conv/D1:2", "conv/D3:4"]}}}})
    item = BenchmarkItem("synthetic", "Question", "Reference", sources=[
        {"id": "conv/D1:2", "text": "First"}, {"id": "conv/D3:4", "text": "Second"}],
        extras={"gold_evidence": ["D1:2; D3:4"]})
    annotate_posthoc(row, item)
    diagnostic = row.extras["posthoc_gold"]
    assert diagnostic["all_gold_visible"] is True
    assert diagnostic["unavailable_gold_ids"] == []
    assert diagnostic["stages"]["final_candidates"]["all_gold_ids_present"] is True
    assert diagnostic["normalization_version"] == "compound-locators-v2"
    assert row.correct is False and row.score == 0
