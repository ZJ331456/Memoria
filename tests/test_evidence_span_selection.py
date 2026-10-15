"""Engineering selection cases; no benchmark labels enter the selector."""
import pytest

from memoria.claims import canonical
from memoria.memory.evidence_selector import select_evidence


def item(cid, start, end, *, sid="source", fingerprint="a"*64, kind="source", score=1):
    return dict(id=cid, kind=kind, score=score, covers=[cid], source_ids=[sid], data={
        "text":cid, "evidence":dict(message_id=sid, start=start, end=end, content_sha256=fingerprint)})


@pytest.mark.parametrize("strategy", ["topk", "coverage", "mmr", "evidence_coverage"])
def test_disjoint_and_partially_overlapping_spans_survive_id_dedup(strategy):
    pool = [item("from", 0, 20), item("to", 20, 40), item("context", 35, 50)]
    result = select_evidence(pool, ["from", "to", "context"], 5000, strategy)
    assert {e["id"] for e in result["payload"]["evidence"]} == {"from", "to", "context"}
    assert not result["uncovered"]
    assert result["cost_chars"] == len(canonical(result["payload"])) <= 5000


def test_full_union_coverage_drops_contained_copy_and_traces_every_candidate():
    pool = [item("left", 0, 10, score=4), item("right", 10, 20, score=3),
            item("covered", 5, 15, score=2), item("new", 19, 21, score=1)]
    result = select_evidence(pool, [], 5000, "topk")
    assert [e["id"] for e in result["payload"]["evidence"]] == ["left", "right", "new"]
    assert next(t for t in result["trace"] if t["id"] == "covered")["decision"] == "duplicate_source"
    assert {t["id"] for t in result["trace"]} == {e["id"] for e in pool}


def test_different_versions_of_one_source_cannot_mix_coordinates():
    result = select_evidence([item("old", 0, 10, score=2), item("new", 10, 20, fingerprint="b"*64)], [], 5000, "topk")
    assert len(result["payload"]["evidence"]) == 1
    assert result["dropped"] == [{"id":"new", "reason":"source_version_conflict"}]


@pytest.mark.parametrize("bad", [None, -1, True, "0"])
def test_missing_or_invalid_coordinates_remain_conservative(bad):
    other = item("other", 20, 40)
    other["data"]["evidence"]["start"] = bad
    result = select_evidence([item("first", 0, 20, score=2), other], [], 5000, "topk")
    assert result["dropped"] == [{"id":"other", "reason":"duplicate_source"}]


def test_claim_conditions_are_part_of_source_coverage_and_claim_is_indivisible():
    claim = item("claim", 0, 20, kind="claim", score=4)
    claim["data"]["condition_evidence"] = [dict(message_id="condition", start=0, end=10, content_sha256="a"*64)]
    claim["source_ids"].append("condition")
    result = select_evidence([claim, item("copy", 0, 10, sid="condition", score=2),
                             item("context", 10, 20, sid="condition")], [], 5000, "topk")
    assert [e["id"] for e in result["payload"]["evidence"]] == ["claim", "context"]
    assert result["payload"]["evidence"][0]["data"]["condition_evidence"]


def test_metadata_and_repeated_bytes_are_included_in_complete_budget():
    pool = [item("a", 0, 20), item("b", 20, 40)]
    full = select_evidence(pool, [], 5000, "topk")
    cap = full["cost_chars"]-1
    result = select_evidence(pool, [], cap, "topk")
    assert len(result["payload"]["evidence"]) == 1 and result["cost_chars"] <= cap
