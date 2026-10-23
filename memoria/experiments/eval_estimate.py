"""Zero-inference planning estimate. Explicit scenarios, never a fabricated bill."""
from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

from ..llm.deepseek_cache import canonical
from ..memory.reader import SOURCE_CONTRACT
from .deepseek_cache import PRICING
from .eval_judge import JUDGE_PROMPT, JUDGE_SYSTEM_PROMPT
from .eval_systems import source_batches

PROTOCOL = "kernel-vs-context-llm-judge-v1"


def estimate(items, dataset, *, model, context_bytes=240000, arms=None):
    """Estimate all lifecycle phases, stating the unmeasured quantities explicitly."""
    if model not in {"deepseek-flash", "deepseek-v4-pro"}:
        raise ValueError("Only explicitly priced DeepSeek models are supported")
    arms = list(arms) if arms is not None else ["memoria", "raw_context"]
    histories = {}
    for item in items:
        key = hashlib.sha256(canonical(item.sources).encode()).hexdigest()
        histories[key] = item.sources
    histories_chars = sum(len(canonical({"evidence": [{"citation": s["id"],
        "content": s.get("text", ""), "speaker": s.get("speaker", "unknown"),
        "session_date_raw": s.get("session_date_raw", "")} for s in item.sources]}))
        + len(SOURCE_CONTRACT) + len(item.question) for item in items)
    batches = [b for history in histories.values() for b in source_batches(history)]
    n, b = len(items), len(batches)
    scenarios = []
    # Assumptions are deliberately exposed: a local BGE tokenizer is NOT a DeepSeek tokenizer.
    for name, chars_per_token, hit, kernel_calls, kernel_input, formation_calls in (
        ("warm_optimistic", 4.0, 0.90, 3, 2500, 2),
        ("planning", 3.0, 0.50, 5, 4500, 3),
        ("cold_stress", 2.5, 0.00, 8, 8000, 5),
    ):
        phases = {
            "baseline_answers": {"calls": n, "input_tokens": histories_chars/chars_per_token,
                                 "output_tokens": n*256},
            "kernel_answers_and_checks": {"calls": n*kernel_calls,
                "input_tokens": n*kernel_calls*kernel_input, "output_tokens": n*kernel_calls*500},
            "kernel_formation": {"calls": b*formation_calls,
                "input_tokens": sum(len(v["content"]) for v in batches)/chars_per_token*formation_calls
                    + b*formation_calls*1500,
                "output_tokens": b*formation_calls*1500},
            "independent_judge": {"calls": n*len(arms),
                "input_tokens": n*len(arms)*(len(JUDGE_PROMPT)/chars_per_token+300),
                "output_tokens": n*len(arms)*160},
        }
        if "raw_context" not in arms:
            phases.pop("baseline_answers")
        if "memoria" not in arms:
            phases.pop("kernel_answers_and_checks")
            phases.pop("kernel_formation")
        if "source_only" in arms:
            phases["source_only_answers_and_checks"] = {"calls": n*kernel_calls,
                "input_tokens": n*kernel_calls*kernel_input, "output_tokens": n*kernel_calls*500}
        if "rrf_only" in arms:
            phases["rrf_only_answers"] = {"calls": n,
                "input_tokens": n*kernel_input, "output_tokens": n*500}
        for phase in phases.values():
            phase["input_tokens"] = round(phase["input_tokens"])
            phase["assumed_cache_hit_rate"] = hit
            phase["usd"] = {period: round((phase["input_tokens"]*(hit*r["hit"]+(1-hit)*r["miss"])
                                   + phase["output_tokens"]*r["output"])/1e6, 4)
                            for period, r in PRICING[model].items()}
        scenarios.append({"scenario": name, "assumptions": {"chars_per_token": chars_per_token,
            "cache_hit_rate": hit, "kernel_calls_per_question": kernel_calls,
            "formation_calls_per_window": formation_calls}, "phases": phases,
            "requests": sum(p["calls"] for p in phases.values()),
            "total_usd": {period: round(sum(p["usd"][period] for p in phases.values()), 4)
                          for period in PRICING[model]}})
    return {"protocol": PROTOCOL, "mode": "estimate_only", "model_calls": 0,
        "model": model, "dataset_sha256": hashlib.sha256(Path(dataset).read_bytes()).hexdigest(),
        "arms": arms,
        "judge_reference": "https://github.com/mem0ai/memory-benchmarks/blob/4b61c5d31b9c668a12b4f5e78064248a02c82d2b/benchmarks/locomo/prompts.py",
        "judge_reference_policy": "adapted binary rubric, no evidence override; not an exact Mem0 score reproduction",
        "questions_per_arm": len(items), "groups": dict(Counter(i.group for i in items)),
        "histories": len(histories), "source_turns": sum(len(h) for h in histories.values()),
        "formation_windows": b, "context_byte_limit": context_bytes,
        "baseline_untruncated_serialized_chars_sum": histories_chars,
        "scenarios": scenarios, "pricing_snapshot": PRICING,
        "judge_prompt_sha256": hashlib.sha256((JUDGE_SYSTEM_PROMPT+JUDGE_PROMPT).encode()).hexdigest(),
        "limits": ["Planning assumptions, not measured DeepSeek tokens or a provider quote.",
                   "Baseline estimate uses untruncated histories; every requested arm includes judge calls.",
                   "Cache reuse/formation yield/output length are unknown; no hit rate is guaranteed.",
                   "Reruns, failures with unknown usage, and tariff changes can exceed this planning range.",
                   "No paid run is authorized by generating this estimate."]}
