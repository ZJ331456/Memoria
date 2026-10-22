"""Two-arm evaluation. Default is an offline estimate; paid execution is explicit and capped."""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
from pathlib import Path

from ..memory.reader import SOURCE_CONTRACT
from .eval_benchmarks import load_items
from .eval_estimate import estimate, PROTOCOL
from .eval_judge import JUDGE_SYSTEM_PROMPT
from .eval_runner import LlmJudge, build_report, run_arms
from .eval_systems import KernelMemorySystem, RawContextSystem

READER_SYSTEM = SOURCE_CONTRACT


async def run(workspace, settings, *, benchmark, dataset, arms=None, conversations=10,
              questions=None, output=None, concurrency=2, allow_remote=False,
              max_tokens=1024, context_chars=240000, kernel_budget_chars=8000,
              write_mode="library", judge=True, execute=False, max_calls=None, max_cost_usd=None):
    from .runner import account_settings
    from .eval_accounting import EvaluationClient
    from .records import RunRecorder, safe_endpoint
    from ..llm.deepseek_cache import official
    from ..config import ModelConfig

    if sorted(arms or ["raw_context", "memoria"]) != ["memoria", "raw_context"]:
        raise ValueError("Official evaluation requires exactly raw_context and memoria")
    if write_mode != "library" or not judge:
        raise ValueError("Public benchmark requires external sources and independent binary judging")
    if concurrency not in {1, 2} or not 1 <= max_tokens <= 2048 or context_chars < 4096:
        raise ValueError("Invalid explicit evaluation bounds")
    settings = account_settings(copy.deepcopy(settings), workspace)
    items = load_items(benchmark, dataset, **(
        {"conversations": conversations, "questions_per_conversation": questions}
        if benchmark == "locomo" else {"limit": questions}))
    if not items:
        raise ValueError("No benchmark items")
    if len({item.id for item in items}) != len(items):
        raise ValueError("Duplicate benchmark item IDs; rejected before model calls")
    plan = estimate(items, dataset, model=settings.main.model, context_bytes=context_chars)
    if not execute:
        # Crucially no LLM client, kernel, embedding or production database is constructed.
        if output:
            target = Path(output)
            target.mkdir(parents=True, exist_ok=True)
            (target / "estimate.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        return plan
    if not allow_remote or not official(settings.main) or not max_calls or not max_cost_usd:
        raise ValueError("Execution requires --allow-remote and positive --max-calls/--max-cost-usd")
    registration = json.loads((settings.root / "eval/protocols/kernel-vs-context-judge-v1.json").read_text(encoding="utf-8"))
    if benchmark != "locomo" or plan["dataset_sha256"] != registration["source_sha256"]:
        raise ValueError("Paid v1 execution requires the frozen LoCoMo source SHA")
    expected = (registration["backbone"], registration["reader_max_tokens"], registration["concurrency"],
                registration["input_request_byte_cap"], registration["kernel_evidence_chars"],
                registration["judge_prompt_sha256"])
    actual = (settings.main.model, max_tokens, concurrency, context_chars, kernel_budget_chars,
              plan["judge_prompt_sha256"])
    if actual != expected:
        raise ValueError("Model/budgets/judge differ from proposed v1; create a separate registration")
    private_root = workspace.path("experiments").resolve()
    if output and not Path(output).resolve().is_relative_to(private_root):
        raise ValueError("Paid raw ledgers/databases must remain inside this account's private experiments directory")
    settings.main.thinking = "disabled"
    settings.fast = copy.deepcopy(settings.main)
    settings.max_tokens = max_tokens
    settings.max_retries = 0
    settings.request_timeout_seconds = 120
    settings.reader_input_bytes = context_chars
    settings.reader_context_tokens = None
    embedding = settings.root / "model/bge-small-en-v1.5"
    if not embedding.is_dir():
        raise ValueError("Required local English BGE snapshot missing")
    settings.embedding = ModelConfig(str(embedding), "", "local://auto", backend="bge")
    frozen = {**plan, "mode": "execution", "concurrency": concurrency,
        "scope": "full_development_retest" if len(items)==1986 else "diagnostic_subset",
        "concurrency_scope": "parallel arms; questions remain serial within each arm",
        "reader_max_tokens": max_tokens, "temperature": 0, "top_p": 1,
        "timeout_seconds": 120, "max_retries": 0, "thinking": "disabled",
        "kernel_budget_chars": kernel_budget_chars,
        "write_mode": "external-source-session-windows", "max_calls": max_calls,
        "max_cost_usd": max_cost_usd, "cold_warm": "provider best effort, no forced warmup",
        "main_score": "categories 1-4; category 5 is supplementary",
        "interpretation": "same backbone, complete kernel versus raw context; no single-mechanism causal claim",
        "source_time": "raw session clock retained; dataset timezone unknown",
        "resume": "not supported; checkpoints retained, never silently recomputed as free calls"}
    recorder = RunRecorder(Path(output) if output else workspace.path("experiments", PROTOCOL),
        settings.root, datasets=[Path(dataset)], models={
            role: {"model": settings.main.model, "endpoint": safe_endpoint(settings.main.base_url)}
            for role in ("reader", "formation", "judge")},
        protocol=frozen, seed=None, model_paths={"embedding": embedding})
    client = EvaluationClient(settings, recorder, max_calls=max_calls, max_cost_usd=max_cost_usd)

    def factory(database):
        from ..memory.kernel import MemoryKernel
        from ..store import Store
        kernel_settings = copy.deepcopy(settings)
        kernel_settings.database, kernel_settings.markdown_enabled = database, False
        return MemoryKernel(kernel_settings, Store(database, vector_backend="json"),
                            llm=client, user_id=workspace.owner)

    systems = {"raw_context": RawContextSystem(client, system=READER_SYSTEM,
                    max_tokens=max_tokens, budget_chars=context_chars),
               "memoria": KernelMemorySystem(factory, max_tokens=max_tokens,
                    budget_chars=kernel_budget_chars, directory=recorder.path / "kernel")}
    try:
        results = await run_arms(systems, items, benchmark=benchmark, concurrency=concurrency,
            judge=LlmJudge(client, system=JUDGE_SYSTEM_PROMPT),
            checkpoint=lambda result: recorder.append("cases.jsonl", result.to_dict()))
        accounting = client.accounting()
        payload = build_report(recorder.path, benchmark=benchmark,
            metadata={**frozen, "accounting": accounting}, results_by_arm=results)
        errors = sum(bool(r.error) for rows in results.values() for r in rows)
        recorder.finish({"errors": errors, "accounting": accounting,
                         "summaries": payload["summaries"]})
        payload["run_id"] = recorder.run_id
        return payload
    except BaseException as exc:
        recorder.finish({"errors": 1, "accounting": client.accounting()}, error=type(exc).__name__)
        raise
    finally:
        systems["memoria"].close()


def main(argv=None):
    from .history import HistoryWorkspace
    from ..config import Settings
    from scripts.user_paths import directory_for
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--benchmark", default="locomo", choices=["locomo", "longmemeval"])
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--conversations", type=int, default=10)
    parser.add_argument("--questions", type=int)
    parser.add_argument("--output")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--max-cost-usd", type=float)
    args = parser.parse_args(argv)
    result = asyncio.run(run(HistoryWorkspace(directory_for(args.user)), Settings.load(),
                             **{k: v for k, v in vars(args).items() if k != "user"}))
    print(json.dumps(result if not args.execute else result["summaries"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
