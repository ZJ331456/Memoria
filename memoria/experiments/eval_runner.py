"""Run one benchmark against the two arms and write one comparable report.

The runner is deliberately small: it ingests sources into each arm, asks each question,
grades the answers with one shared judge and writes the unified files. Everything else
(which benchmark, which arms, which judge) is a parameter.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Callable

from .eval_judge import build_judge_prompt, parse_judgment
from .eval_schema import ArmResult, ArmSummary, compare, summarize, write_report
from .eval_accounting import phase
from .eval_diagnostics import annotate_posthoc, paired_changes


def parse_answer_payload(text: str) -> dict[str, Any]:
    """Read the answer contract out of a model reply; an unparsable reply is not an answer."""
    import json

    if not text:
        return {"status": "invalid", "answer": "", "evidence_ids": []}
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        return {"status": "invalid", "answer": text.strip(), "evidence_ids": []}
    try:
        payload = json.loads(candidate[start : end + 1])
    except ValueError:
        return {"status": "invalid", "answer": text.strip(), "evidence_ids": []}
    if not isinstance(payload, dict):
        return {"status": "invalid", "answer": text.strip(), "evidence_ids": []}
    return {
        "status": payload.get("status") if isinstance(payload.get("status"), str) and payload.get("status") in {"answer", "clarify", "abstain"}
            and isinstance(payload.get("answer"), str) and bool(payload["answer"].strip())
            and isinstance(payload.get("evidence_ids"), list)
            and all(isinstance(v, str) for v in payload["evidence_ids"]) else "invalid",
        "answer": str(payload.get("answer") or ""),
        "evidence_ids": payload.get("evidence_ids") if isinstance(payload.get("evidence_ids"), list) else [],
    }


class LlmJudge:
    """Grades an answer with a separate model call that never sees the arm name."""

    def __init__(self, client, *, system: str, max_tokens: int = 700):
        self.client, self.system, self.max_tokens = client, system, max_tokens

    async def grade(self, question: str, gold: str, answer: str) -> dict[str, Any]:
        prompt = build_judge_prompt(question, gold, answer)
        try:
            result = await self.client.chat(
                [{"role": "system", "content": self.system}, {"role": "user", "content": prompt}],
                max_tokens=self.max_tokens,
                temperature=0,
            )
        except Exception as exc:
            return {"label": "UNKNOWN", "reason": f"{type(exc).__name__}: {exc}", "usage": None}
        verdict = parse_judgment(result.content)
        verdict["usage"] = result.usage or None
        return verdict


class ArmRunner:
    """Drives one memory system over a list of benchmark items.

    Every item starts from a clean system and ingests exactly the sources that were visible
    when the question was asked. Without the reset, a later question would also see the
    earlier question's sources and the arms would no longer answer the same question.
    """

    def __init__(self, system, *, judge: LlmJudge | None = None, name: str | None = None, checkpoint=None, stop_reason=None):
        self.system = system
        self.judge = judge
        self.name = name or getattr(system, "name", "arm")
        self.checkpoint = checkpoint
        self.stop_reason = stop_reason or (lambda: None)

    async def run(self, items, *, ground_truth_field: str = "ground_truth") -> list[ArmResult]:
        results: list[ArmResult] = []
        for item in items:
            started = time.perf_counter()
            result = ArmResult(
                id=item.id,
                arm=self.name,
                question=item.question,
                ground_truth=getattr(item, ground_truth_field),
                group=item.group,
            )
            if reason := self.stop_reason():
                result.error = "not_run:" + reason
                annotate_posthoc(result, item)
                results.append(result)
                if self.checkpoint:
                    self.checkpoint(result)
                continue
            try:
                reset = getattr(self.system, "reset", None)
                with phase(self.name, "formation", item.id):
                    if reset is not None:
                        reset(item.sources)
                    for source in item.sources:
                        await self.system.ingest(source)
                    drain = getattr(self.system, "drain", None)
                    if drain is not None:
                        await drain()
                if reason := self.stop_reason():
                    raise RuntimeError("evaluation_stopped:" + reason)
                with phase(self.name, "answer_and_support", item.id):
                    answer = await self.system.answer(item.question)
            except Exception as exc:  # a failing item is reported, never silently dropped
                result.error = f"{type(exc).__name__}: {exc}"
                result.latency_ms = round((time.perf_counter() - started) * 1000, 3)
                annotate_posthoc(result, item)
                results.append(result)
                if self.checkpoint:
                    self.checkpoint(result)
                continue
            raw = answer.get("answer") or ""
            parsed = parse_answer_payload(raw)
            result.answer = parsed["answer"] or raw
            result.answer_status = parsed["status"]
            result.evidence_ids = parsed["evidence_ids"] or list(answer.get("evidence_ids") or [])
            result.retrieved = list(answer.get("retrieved") or [])
            result.usage = answer.get("usage")
            result.latency_ms = answer.get("latency_ms") or round(
                (time.perf_counter() - started) * 1000, 3
            )
            result.extras = dict(answer.get("extras") or {})
            if answer.get("error"):
                result.error = answer["error"]
            elif parsed["status"] == "invalid":
                result.error = "invalid_answer_contract"
            if self.judge is not None and not result.error:
                with phase(self.name, "independent_judge", item.id):
                    verdict = await self.judge.grade(item.question, result.ground_truth, result.answer)
                result.judgment = verdict["label"]
                result.reason = verdict.get("reason", "")
                if verdict["label"] == "UNKNOWN":
                    result.score = None
                    result.correct = None
                    result.error = "judge_unknown"
                else:
                    result.score = 1.0 if verdict["label"] == "CORRECT" else 0.0
                    result.correct = verdict["label"] == "CORRECT"
                result.extras["judge"] = verdict
            annotate_posthoc(result, item)
            results.append(result)
            if self.checkpoint:
                self.checkpoint(result)
        return results


def score_locomo(prediction: str, reference: str, category: str) -> float:
    """Category-aware overlap score, the same rule the frozen LoCoMo runs used."""
    from .locomo_eval import answer_f1

    try:
        value = int(category)
    except (TypeError, ValueError):
        value = 0
    return answer_f1(prediction, reference, value)


def score_exact(prediction: str, reference: str, category: str) -> float:
    """Exact match after normalisation; used where the reference is a short literal."""
    import re
    import string

    def clean(text: str) -> str:
        text = text.lower().translate(str.maketrans("", "", string.punctuation))
        return re.sub(r"\s+", " ", text).strip()

    return 1.0 if clean(prediction) == clean(reference) and clean(reference) else 0.0


SCORERS = {"locomo": score_locomo, "exact": score_exact}


def build_report(
    directory: str | Path,
    *,
    benchmark: str,
    metadata: dict[str, Any],
    results_by_arm: dict[str, list[ArmResult]],
) -> dict[str, Any]:
    """Summarize every arm and, when a baseline is present, state the delta explicitly."""
    summaries: list[ArmSummary] = []
    for arm, results in results_by_arm.items():
        summaries.append(summarize(arm, results))
    payload = write_report(
        directory, benchmark=benchmark, metadata=metadata, summaries=summaries,
        results=[r for results in results_by_arm.values() for r in results],
    )
    by_name = {summary.arm: summary for summary in summaries}
    comparisons = []
    baseline = by_name.get("raw_context")
    if baseline is not None:
        for name, summary in by_name.items():
            if name == "raw_context":
                continue
            comparisons.append(compare(baseline, summary))
    for baseline_name, treatment_name in (("source_only", "memoria"), ("rrf_only", "source_only")):
        if baseline_name in by_name and treatment_name in by_name:
            comparisons.append(compare(by_name[baseline_name], by_name[treatment_name]))
    for comparison in comparisons:
        comparison["paired_changes"] = paired_changes(results_by_arm[comparison["baseline_arm"]],
            results_by_arm[comparison["treatment_arm"]])
    payload["comparisons"] = comparisons
    Path(directory, "report.json").write_text(
        __import__("json").dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


async def run_arms(
    arms: dict[str, Any],
    items,
    *,
    benchmark: str = "locomo",
    scorer: Callable[[str, str, str], float] | None = None,
    concurrency: int = 1,
    judge: LlmJudge | None = None,
    checkpoint=None,
    stop_reason=None,
) -> dict[str, list[ArmResult]]:
    """Run every arm over the same items. Arms are independent of each other."""
    items = list(items)  # A generator must not disappear after the first arm.
    results: dict[str, list[ArmResult]] = {}
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def run_one(name: str, system: Any) -> tuple[str, list[ArmResult]]:
        async with semaphore:
            runner = ArmRunner(system, judge=judge, name=name, checkpoint=checkpoint, stop_reason=stop_reason)
            return name, await runner.run(items)

    if concurrency == 1:
        # Explicit ordering: finish all Memoria items before spending on Baseline.
        outcomes = []
        for name, system in arms.items():
            outcomes.append(await run_one(name, system))
    else:
        outcomes = await asyncio.gather(*(run_one(name, system) for name, system in arms.items()))
    for name, values in outcomes:
        results[name] = values
    return results


async def run_ordered_histories(factories, items, *, concurrency, judge=None,
                                checkpoint=None, stop_reason=None):
    """Bounded parallel independent histories, with entire arms strictly ordered.

    A system/kernel is never shared by two concurrent questions. Source hashes
    (including role/text/time) define isolation; gold never controls grouping.
    """
    import hashlib
    from ..llm.deepseek_cache import canonical
    items = list(items)
    groups = {}
    for item in items:
        signature = hashlib.sha256(canonical(item.sources).encode()).hexdigest()
        groups.setdefault(signature, []).append(item)
    results = {}
    for name, factory in factories.items():
        semaphore = asyncio.Semaphore(concurrency)
        async def run_history(signature, group):
            async with semaphore:
                system = factory(signature)
                try:
                    return await ArmRunner(system, name=name, judge=judge,
                        checkpoint=checkpoint, stop_reason=stop_reason).run(group)
                finally:
                    close = getattr(system, 'close', None)
                    if close:
                        close()
        completed = await asyncio.gather(*(run_history(key, group) for key, group in groups.items()))
        by_id = {row.id: row for group in completed for row in group}
        results[name] = [by_id[item.id] for item in items]
    return results
