"""Read-only cache audit of immutable run ledgers; separate report, zero inference."""

import json
import re
from datetime import datetime, timezone
from types import SimpleNamespace

from ..llm.deepseek_cache import (
    cache_summary,
    hit_rate_drop,
    official,
    physical_input,
)
from .locomo_diagnostics import lineage, verified_run, lines
from .records import digest_file

PRICING = {
    "as_of": "2026-10-22",
    "currency": "USD",
    "unit": "per_million_tokens",
    "source": "https://api-docs.deepseek.com/quick_start/pricing/",
    "deepseek-flash": {
        "off_peak": {"hit": 0.003, "miss": 0.15, "output": 0.6},
        "peak": {"hit": 0.006, "miss": 0.3, "output": 1.2},
    },
    "deepseek-v4-pro": {
        "off_peak": {"hit": 0.022, "miss": 0.66, "output": 1.98},
        "peak": {"hit": 0.044, "miss": 1.32, "output": 3.96},
    },
}


def cost_bounds(summary, model, table=None):
    rates = (table or PRICING).get(model)
    if not rates:
        return None
    result = {}
    for period, rate in rates.items():
        hit, miss = summary["prompt_cache_hit_tokens"], summary["prompt_cache_miss_tokens"]
        input_cost = (hit * rate["hit"] + miss * rate["miss"]) / 1e6
        result[period] = {
            "known_input_usd": input_cost,
            "known_input_output_usd": input_cost
            + summary["completion_tokens_known"] * rate["output"] / 1e6,
            "effective_input_usd_per_million": (
                input_cost * 1e6 / (hit + miss) if hit + miss else None
            ),
            "input_without_cache_usd": (hit + miss) * rate["miss"] / 1e6,
        }
    return {
        "rates": rates,
        "estimate": result,
        "invoice": False,
        "complete_usage": summary["complete"] and summary["unknown_completion_calls"] == 0,
        "scope": "known valid usage only; pricing snapshot, not historical billing; no inferred holiday/period",
    }


VOLATILE_MARKERS = {
    "iso_timestamp": re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"),
    "clock_time": re.compile(r"\b\d{1,2}:\d{2}:\d{2}\b"),
    "hex_uuid": re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}"),
    "uuid_hex32": re.compile(r"\b[0-9a-f]{32}\b"),
    "float_score": re.compile(r"\b0\.\d{6,}\b"),
}


def prefix_stability(prompts):
    """Deterministic offline check: how much of each role's prompt is a stable head.

    No model calls and no rewriting. A volatile marker that survives inside the shared
    head means the "stable" block still moves between runs, which is the usual cause of a
    sudden hit-rate drop.
    """

    def text(messages):
        return "\n\n".join(
            str(m.get("role", "")) + ":" + (m.get("content") or "") for m in messages
        )

    groups = {}
    for row in prompts:
        messages = row.get("messages")
        if isinstance(messages, list) and messages:
            groups.setdefault(row.get("role") or "unknown", []).append(text(messages))
    report = {}
    for role, texts in sorted(groups.items()):
        head = texts[0]
        for value in texts[1:]:
            limit = min(len(head), len(value))
            index = 0
            while index < limit and head[index] == value[index]:
                index += 1
            head = head[:index]
            if not head:
                break
        sizes = sorted(len(t) for t in texts)
        median = sizes[len(sizes) // 2]
        report[role] = {
            "prompts": len(texts),
            "stable_head_chars": len(head),
            "median_prompt_chars": median,
            "stable_head_ratio": (len(head) / median) if median else None,
            "volatile_markers_in_stable_head": [
                name for name, pattern in VOLATILE_MARKERS.items() if pattern.search(head)
            ],
        }
    return report


def audit(workspace, run_id, *, include_lineage=False):
    path, manifest = verified_run(workspace, run_id)
    files = [
        path / n for n in ("manifest.json", "calls.jsonl", "prompts.jsonl") if (path / n).exists()
    ]
    before = {p.name: digest_file(p) for p in files}
    if include_lineage:
        _, _, calls = lineage(workspace, run_id)
    else:
        calls = lines(path / "calls.jsonl")
    grouped = {}
    excluded = 0
    for call in calls:
        origin = manifest
        if call.get("origin_run_id") and call["origin_run_id"] != run_id:
            _, origin = verified_run(workspace, call["origin_run_id"])
        role = call.get("role", "unknown")
        model = origin["models"].get(
            role, origin["models"].get("assessor", {}) if role == "judge" else {}
        )
        if not official(SimpleNamespace(base_url=model.get("endpoint", ""))):
            excluded += 1
            continue
        grouped.setdefault((role, model["model"]), []).append(call)
    roles = []
    for (role, model), rows in sorted(grouped.items()):
        summary = cache_summary(rows)
        pricing = cost_bounds(summary, model)
        roles.append(
            {
                "role": role,
                "model": model,
                "usage": summary,
                "pricing": pricing,
                # A model id the snapshot does not know must be visible, never an empty field
                # that reads as "no cost".
                "pricing_unavailable": pricing is None,
            }
        )
    included = [call for rows in grouped.values() for call in rows]
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "scope": "verified lineage" if include_lineage else "this run's recorded calls only",
        "calls": len(calls),
        "included_deepseek_calls": len(included),
        "excluded_non_deepseek_or_unknown_provider": excluded,
        "overall": cache_summary(included),
        "roles": roles,
        # Input volume and provider tokens are distinct units. Cache hits discount fees,
        # not the number of prompt tokens or the required context window.
        "physical_input": physical_input(included),
        # A sudden fall is reported with its most likely cause instead of being guessed at.
        "hit_rate_trend": hit_rate_drop(included),
        "pricing_snapshot": PRICING,
        "model_calls": 0,
        "source_artifacts": before,
        "prefix_stability": (
            prefix_stability(lines(path / "prompts.jsonl"))
            if (path / "prompts.jsonl").exists()
            else None
        ),
        "limitations": [
            "token weighted, not average of per-call percentages",
            "cold calls and short prefixes can miss",
            "adjacent prompt LCP is a proxy, not a proof of provider cache reuse",
            "no new QA score or historical invoice claim",
            "prefix_stability reports a deterministic stable head, not a provider hit prediction",
        ],
    }
    assert before == {p.name: digest_file(p) for p in files}, "Run ledger changed during audit"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = workspace.path("reports", "deepseek-cache-" + run_id + "-" + stamp)
    target.mkdir(parents=True, exist_ok=False)
    (target / "aggregate.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target, report


async def probe(workspace, settings, source, *, execute=False, allow_remote=False):
    """Two fixed official questions, <=16 paid calls, same bounded evidence in both arms.

    This measures layout costs/contracts, not a new benchmark/generalization score.
    Gate outputs are never supplied to the independent Reader or Support verifier.
    """
    import copy
    import time
    from .runner import account_settings
    from .roles import ExperimentLLM, RoleConfig
    from .records import RunRecorder
    from .locomo_eval import benchmark_cases, SYSTEM, parse_answer, answer_f1
    from .locomo import SOURCE_SHA256
    from ..memory.evidence_sufficiency import SYSTEM as GATE, EvidenceSufficiency
    from ..memory.answer_risk import SYSTEM as SUPPORT, AnswerRisk
    from ..llm.deepseek_cache import shared_reader_messages, VERSION, COMMON_SYSTEM
    from ..memory.verification import verify
    from ..memory.reader import strict_json
    from ..observability.timing import StageTimings
    from ..prompting.reader_budget import ReaderBudget

    if digest_file(source) != SOURCE_SHA256:
        raise ValueError("Expected frozen official LoCoMo input")
    settings = account_settings(copy.deepcopy(settings), workspace, None)
    if not official(settings.main):
        raise ValueError("Probe is restricted to official DeepSeek")
    settings.main.thinking = "disabled"
    if not execute:
        return {
            "planned_cases": 2,
            "planned_call_limit": 16,
            "max_input_bytes": 500000,
            "remote_authorized": allow_remote,
            "executed": False,
        }
    if not allow_remote:
        raise ValueError("Explicit --allow-remote required")
    raw = strict_json(source.read_text(encoding="utf-8"))
    cases, histories, _ = benchmark_cases(raw, "micro")
    roles = {
        role: RoleConfig(
            slot="main",
            max_tokens=1024 if role == "reader" else 2048,
            thinking="disabled_by_deepseek_api",
        )
        for role in ("reader", "judge")
    }
    recorder = RunRecorder(
        workspace.path("experiments"),
        settings.root,
        datasets=[source],
        models={},
        seed=None,
        protocol={
            "id": "deepseek-cache-layout-probe-v1",
            "cases": 2,
            "max_calls": 16,
            "max_input_bytes": 500000,
            "owner_user_id": workspace.owner,
            "cache_layout": VERSION,
            "common_system_sha256": __import__("hashlib")
            .sha256(COMMON_SYSTEM.encode())
            .hexdigest(),
            "source_kind": "official_public_benchmark",
            "quality_scope": "two seen questions, development diagnostic only",
            "online_excludes": ["gold answers", "QA category", "gold evidence"],
            "selection": "first two original questions, first48 chronological utterances of first conversation; all modes same evidence",
            "control": "legacy independent stage prompts",
            "treatment": "same exact evidence/contracts; stable evidence prefix; independent calls",
        },
    )
    client = ExperimentLLM(settings, roles, recorder, StageTimings())
    recorder.manifest["models"] = client.public_roles()
    recorder._save_manifest()
    used_bytes = 0
    rows = []

    async def call(arm, stage, system, packet):
        nonlocal used_bytes
        role = "reader" if stage == "reader" else "judge"
        cfg = roles[role]
        messages = shared_reader_messages(
            settings.main,
            SYSTEM,
            {"question": case["question"], "evidence": evidence},
            system,
            packet,
            enabled=arm == "optimized",
        )
        prepared = await ReaderBudget(settings).prepare(
            messages,
            selected=settings.main,
            max_tokens=cfg.max_tokens,
            temperature=cfg.temperature,
            top_p=cfg.top_p,
            atomic=True,
        )
        if len(client.calls) >= 16 or used_bytes + prepared.report["input_bytes"] > 500000:
            raise ValueError("Probe budget exhausted; no extra requests sent")
        used_bytes += prepared.report["input_bytes"]
        index = len(client.calls)
        try:
            return await client.call(role, prepared.messages)
        finally:
            if len(client.calls) > index:
                client.calls[-1].update(arm=arm, stage=stage)
            # Cost identity is also in the append-only event log, without changing raw calls.
            recorder.append(
                "events.jsonl",
                {
                    "call": index,
                    "case_id": case["id"],
                    "arm": arm,
                    "stage": stage,
                    "input_bytes": prepared.report["input_bytes"],
                },
            )

    fatal = None
    try:
        for i, case in enumerate(cases):
            evidence = [
                {k: m[k] for k in ("id", "speaker", "session_date_raw", "text")}
                for m in histories[case["conversation_id"]][:48]
            ]
            for arm in (("legacy", "optimized") if i == 0 else ("optimized", "legacy")):
                started = time.perf_counter()
                row = {
                    "id": case["id"] + "-" + arm,
                    "case_id": case["id"],
                    "arm": arm,
                    "status": "error",
                    "contract_errors": [],
                }
                try:
                    packet = {"question": case["question"], "evidence": evidence}

                    async def bounded(stage, system, data):
                        return await call(arm, stage, system, data)

                    gate = await verify(
                        bounded,
                        "sufficiency",
                        GATE,
                        packet,
                        lambda value: EvidenceSufficiency().validate(value, evidence),
                        row["contract_errors"],
                    )
                    row["gate"] = gate["state"]
                    answer = await call(arm, "reader", SYSTEM, packet)
                    parsed = parse_answer(answer, [e["id"] for e in evidence])
                    row["reader"] = parsed
                    if parsed["status"] == "answer":
                        cited = [e for e in evidence if e["id"] in parsed["evidence_ids"]]
                        support = await verify(
                            bounded,
                            "support",
                            SUPPORT,
                            {**packet, "evidence": cited, "proposed_answer": parsed["answer"]},
                            lambda value: AnswerRisk().validate(
                                value, case["question"], parsed, cited
                            ),
                            row["contract_errors"],
                        )
                        row["support"] = support["accepted"]
                    else:
                        row["support_skipped"] = "reader_abstained; no pointless verifier call"
                    row["status"] = "success"
                except Exception as exc:
                    row["error"] = type(exc).__name__
                row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
                rows.append(row)
                recorder.append("cases.jsonl", row)
        # Only now load reference labels; never into prompts or route decisions.
        _, _, gold = benchmark_cases(raw, "micro")
        gold = {g["id"]: g for g in gold}
        for row in rows:
            g = gold[row["case_id"]]
            row["posthoc_f1"] = answer_f1(
                row.get("reader", {}).get("answer", ""), g["answer"], g["category"]
            )
    except BaseException as exc:
        fatal = type(exc).__name__
        raise
    finally:
        by_arm = {
            arm: {
                "cache": cache_summary([c for c in client.calls if c.get("arm") == arm]),
                "by_stage": {
                    stage: cache_summary(
                        [c for c in client.calls if c.get("arm") == arm and c.get("stage") == stage]
                    )
                    for stage in ("sufficiency", "reader", "support")
                },
                "pricing": cost_bounds(
                    cache_summary([c for c in client.calls if c.get("arm") == arm]),
                    settings.main.model,
                ),
            }
            for arm in ("legacy", "optimized")
        }
        metrics = {
            "requested_cases": 2,
            "layout_cases": len(rows),
            "calls": len(client.calls),
            "input_bytes": used_bytes,
            "errors": sum(r["status"] != "success" for r in rows),
            "arms": by_arm,
            "quality_rows": rows,
            "production_writes": 0,
            "quality": "development diagnostic only; no full-score or semantic-equivalence claim",
        }
        recorder.finish(metrics, error=fatal)
    return recorder.path, metrics
