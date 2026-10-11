from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class EvaluationReport:
    cases: int
    recall_at_k: float
    precision_at_k: float
    mean_reciprocal_rank: float
    wrong_injection_rate: float
    forbidden_hit_rate: float
    gating_accuracy: float


def evaluate_rankings(cases: list[dict[str, Any]], predictions: dict[str, list[str]], k: int = 5) -> EvaluationReport:
    """Score retrieval output by expected IDs; empty expected sets are negative/gating cases."""
    recalls, precisions, reciprocal, wrong, negatives, forbidden_hits, forbidden_cases, gating_hits = [], [], [], 0, 0, 0, 0, 0
    for case in cases:
        case_id = str(case["id"])
        expected = {str(item) for item in case.get("expected_ids", [])}
        ranked = [str(item) for item in predictions.get(case_id, [])[:k]]
        forbidden = {str(item) for item in case.get("forbidden_ids", [])}
        if forbidden:
            forbidden_cases += 1
            forbidden_hits += int(bool(forbidden.intersection(ranked)))
        if not expected:
            negatives += 1
            wrong += int(bool(ranked))
            gating_hits += int(not ranked)
            continue
        hits = expected.intersection(ranked)
        recalls.append(len(hits) / len(expected))
        precisions.append(len(hits) / max(1, len(ranked)))
        gating_hits += int(bool(hits))
        ranks = [ranked.index(item) + 1 for item in expected if item in ranked]
        reciprocal.append(1 / min(ranks) if ranks else 0.0)
    return EvaluationReport(
        len(cases), round(sum(recalls) / max(1, len(recalls)), 4), round(sum(precisions) / max(1, len(precisions)), 4),
        round(sum(reciprocal) / max(1, len(reciprocal)), 4), round(wrong / max(1, negatives), 4),
        round(forbidden_hits / max(1, forbidden_cases), 4), round(gating_hits / max(1, len(cases)), 4),
    )
