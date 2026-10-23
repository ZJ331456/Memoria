"""Evaluation contract shared by every memory benchmark.

One result schema, one metrics definition, one way to compare a memory kernel against the
same model without the kernel. Shared file shapes do not make different datasets,
judges or protocols directly comparable.

Design mirrors the shape used by comparable projects: a benchmark adapter, a memory
system under test, a plain-model baseline arm, and a judge that never sees the arm it
grades.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = "memoria-eval-1"


@dataclass
class ArmResult:
    """One evaluated arm for one item: what was retrieved, answered and judged."""

    id: str
    arm: str
    question: str = ""
    ground_truth: str = ""
    group: str = ""
    retrieved: list[dict[str, Any]] = field(default_factory=list)
    answer: str = ""
    answer_status: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    correct: bool | None = None
    score: float | None = None
    judgment: str = ""
    reason: str = ""
    error: str | None = None
    usage: dict[str, Any] | None = None
    cache: dict[str, Any] | None = None
    latency_ms: float | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ArmSummary:
    """Aggregated numbers for one arm. Unknown values stay unknown, never become zero."""

    arm: str
    item_ids: list[str] = field(default_factory=list)
    items: int = 0
    scored: int = 0
    errors: int = 0
    correct: int = 0
    accuracy: float | None = None
    accuracy_interpretation: str = "correct / all planned; lower bound if scoring incomplete"
    avg_score: float | None = None
    by_group: dict[str, dict[str, Any]] = field(default_factory=dict)
    primary: dict[str, Any] = field(default_factory=dict)
    tokens: dict[str, Any] = field(default_factory=dict)
    cost: dict[str, Any] = field(default_factory=dict)
    cache: dict[str, Any] = field(default_factory=dict)
    latency_ms: float | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize(arm: str, results: list[ArmResult], *, pass_threshold: float = 0.5) -> ArmSummary:
    """Primary accuracy keeps every planned item; grading failures remain explicit unknowns."""
    if len({r.id for r in results})!=len(results):
        raise ValueError('Duplicate evaluation item IDs')
    summary=ArmSummary(arm=arm,items=len(results))
    summary.item_ids=[r.id for r in results]
    summary.scored=sum(not r.error and r.score is not None for r in results)
    summary.errors=sum(bool(r.error) for r in results)
    summary.correct=sum(not r.error and r.score==1 for r in results)
    summary.accuracy=summary.correct/summary.items if summary.items else None
    valid=[r.score for r in results if not r.error and r.score is not None]
    summary.avg_score=statistics.mean(valid) if valid else None
    for group in sorted({r.group or 'all' for r in results}):
        rows=[r for r in results if (r.group or 'all')==group]
        good=sum(not r.error and r.score==1 for r in rows)
        summary.by_group[group]={'items':len(rows),'correct':good,'scored':sum(not r.error and r.score is not None for r in rows),
            'accuracy':good/len(rows),'unknown':sum(r.score is None for r in rows)}
    latency=[r.latency_ms for r in results if r.latency_ms is not None]
    summary.latency_ms=statistics.mean(latency) if latency else None
    primary=[r for r in results if r.group in {'1','2','3','4'}] if any(r.group in {'1','2','3','4','5'} for r in results) else results
    summary.primary={'items':len(primary),'correct':sum(not r.error and r.score==1 for r in primary),
        'scored':sum(not r.error and r.score is not None for r in primary),
        'accuracy':sum(not r.error and r.score==1 for r in primary)/len(primary) if primary else None,
        'scope':'LoCoMo categories 1-4; category 5 separately' if primary!=results else 'all requested items'}
    # Per-item usage is descriptive only. Authoritative total includes ingestion and judge calls.
    known=[r.usage for r in results if r.usage]
    summary.tokens={'scope':'answer only; full lifecycle costs are in report accounting',
        'calls_without_usage':sum(not r.usage for r in results),
        'total_tokens_known':sum(u.get('total_tokens',0) for u in known if type(u.get('total_tokens')) is int)}
    from .eval_diagnostics import summarize_diagnostics
    summary.diagnostics = summarize_diagnostics(results)
    return summary


def compare(baseline: ArmSummary,treatment: ArmSummary)->dict[str,Any]:
    same=bool(baseline.items) and set(baseline.item_ids)==set(treatment.item_ids)
    return {'baseline_arm':baseline.arm,'treatment_arm':treatment.arm,
        'items_baseline':baseline.items,'items_treatment':treatment.items,'same_denominator':same,
        'accuracy_delta':treatment.accuracy-baseline.accuracy if same and baseline.scored==baseline.items and treatment.scored==treatment.items else None,
        'primary_accuracy_delta':treatment.primary['accuracy']-baseline.primary['accuracy'] if same and baseline.primary.get('accuracy') is not None and treatment.primary.get('accuracy') is not None and baseline.primary['scored']==baseline.primary['items'] and treatment.primary['scored']==treatment.primary['items'] else None,
        'note':'full planned denominator; unknown judge results reported separately' if same else 'different item IDs; no delta is claimed',
        'by_group':{k:{'items_baseline':baseline.by_group.get(k,{}).get('items',0),
            'items_treatment':treatment.by_group.get(k,{}).get('items',0)} for k in set(baseline.by_group)|set(treatment.by_group)}}


def write_report(directory, *, benchmark: str, metadata: dict[str, Any],
                 summaries: list[ArmSummary], results: list[ArmResult]) -> dict[str, Any]:
    """Write the unified result files. Same names and shape for every benchmark."""
    from pathlib import Path

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "benchmark": benchmark,
        "metadata": metadata,
        "summaries": [summary.to_dict() for summary in summaries],
        "results": [result.to_dict() for result in results],
    }
    (target / "summary.json").write_text(
        json.dumps(payload["summaries"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (target / "results.jsonl").write_text(
        "".join(
            json.dumps(result.to_dict(), ensure_ascii=False) + "\n" for result in results
        ),
        encoding="utf-8",
    )
    (target / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload
