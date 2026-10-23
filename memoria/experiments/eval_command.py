"""Two-arm evaluation. Default is an offline estimate; paid execution is explicit and capped."""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
from pathlib import Path

from ..memory.reader import SOURCE_CONTRACT
from .eval_benchmarks import load_items
from .eval_estimate import estimate
from .eval_judge import JUDGE_SYSTEM_PROMPT
from .eval_runner import LlmJudge, build_report, run_arms, run_ordered_histories
from .eval_systems import KernelMemorySystem, RawContextSystem

READER_SYSTEM = SOURCE_CONTRACT
ARM_CONFIGURATION = {
    "memoria": {"formation": True, "include_formed_memory": True, "adaptive_gate_supplement_support": True},
    "source_only": {"formation": False, "include_formed_memory": False, "adaptive_gate_supplement_support": True},
    "rrf_only": {"formation": False, "include_formed_memory": False, "adaptive_gate_supplement_support": False},
    "raw_context": {"full_visible_history": True},
}


def registered_arm_order(arms, registration):
    """An ablation changes the protocol: require exact arm, order and mechanism registration."""
    registered = registration.get("arms", ["memoria", "raw_context"])
    if (not isinstance(registered, list) or len(registered) != len(set(registered))
            or set(registered) != set(arms)):
        raise ValueError("Requested arms differ from frozen registration")
    order = registration.get("arm_order", ["memoria", "raw_context"])
    if not isinstance(order, list) or len(order) != len(set(order)) or set(order) != set(arms):
        raise ValueError("Registered arm_order must list every requested arm exactly once")
    if set(arms) - {"memoria", "raw_context"}:
        expected = {name: ARM_CONFIGURATION[name] for name in arms}
        if registration.get("arm_configuration") != expected:
            raise ValueError("Ablation arm_configuration differs from the executable mechanisms")
    return order


async def run(workspace, settings, *, benchmark, dataset, arms=None, conversations=10,
              questions=None, output=None, concurrency=2, allow_remote=False,
              max_tokens=1024, context_chars=240000, kernel_budget_chars=8000,
              write_mode="library", judge=True, execute=False, max_calls=None, max_cost_usd=None,
              registration=None, history_concurrency=1, item_ids_file=None, include_captions=False):
    from .runner import account_settings
    from .eval_accounting import EvaluationClient
    from .records import RunRecorder, safe_endpoint
    from ..llm.deepseek_cache import official
    from ..config import ModelConfig

    arms = list(arms) if arms is not None else ["memoria", "raw_context"]
    if not arms or len(set(arms)) != len(arms) or set(arms) - ARM_CONFIGURATION.keys():
        raise ValueError("Evaluation requires unique known arms")
    if write_mode != "library" or not judge:
        raise ValueError("Public benchmark requires external sources and independent binary judging")
    if concurrency not in {1, 2} or not 1 <= history_concurrency <= 8 or not 1 <= max_tokens <= 2048 or context_chars < 4096:
        raise ValueError("Invalid explicit evaluation bounds")
    if include_captions and benchmark != "locomo":
        raise ValueError("Dataset-provided caption sources are supported only by the LoCoMo adapter")
    settings = account_settings(copy.deepcopy(settings), workspace)
    items = load_items(benchmark, dataset, **(
        {"conversations": conversations, "questions_per_conversation": questions, "include_captions": include_captions}
        if benchmark == "locomo" else {"limit": questions}))
    if item_ids_file:
        selected=json.loads(Path(item_ids_file).read_text(encoding='utf-8'))
        if not isinstance(selected,list) or not selected or any(not isinstance(i,str) for i in selected) or len(set(selected))!=len(selected):
            raise ValueError('Subset must contain unique explicit item IDs')
        by_id={item.id:item for item in items}
        if set(selected)-by_id.keys():raise ValueError('Subset contains unknown item IDs')
        items=[by_id[mid] for mid in selected]
    if not items:
        raise ValueError("No benchmark items")
    if len({item.id for item in items}) != len(items):
        raise ValueError("Duplicate benchmark item IDs; rejected before model calls")
    plan = estimate(items, dataset, model=settings.main.model, context_bytes=context_chars, arms=arms)
    plan["include_captions"] = include_captions
    plan["source_input_policy"] = ("locomo-dialogue-and-blip-caption-v2" if include_captions
        else "locomo-dialogue-text-v1" if benchmark == "locomo" else "benchmark-text-v1")
    if not execute:
        # Crucially no LLM client, kernel, embedding or production database is constructed.
        if output:
            target = Path(output)
            target.mkdir(parents=True, exist_ok=True)
            (target / "estimate.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        return plan
    if not allow_remote or not official(settings.main) or not max_calls or max_calls <= 0 or not max_cost_usd or max_cost_usd <= 0:
        raise ValueError("Execution requires --allow-remote and positive --max-calls/--max-cost-usd")
    registration = json.loads(Path(registration or settings.root / "eval/protocols/kernel-vs-context-judge-v1.json").read_text(encoding="utf-8"))
    if bool(registration.get("include_captions", False)) != include_captions:
        raise ValueError("Caption input policy differs from frozen registration")
    if registration.get("source_input_policy", "locomo-dialogue-text-v1") != plan["source_input_policy"]:
        raise ValueError("Source input policy differs from frozen registration")
    arm_order = registered_arm_order(arms, registration)
    if set(arms) - {"memoria", "raw_context"} and concurrency != 1:
        raise ValueError("Ablation arms require registered sequential execution")
    if max_calls > registration.get("max_calls", max_calls) or max_cost_usd > registration.get("max_cost_usd", max_cost_usd):
        raise ValueError("Requested execution caps exceed frozen registration")
    if item_ids_file and [item.id for item in items]!=registration.get('item_ids'):
        raise ValueError('Diagnostic subset differs from frozen registration')
    if registration.get('item_ids') and [item.id for item in items]!=registration['item_ids']:
        raise ValueError('Registered diagnostic requires its exact subset; full execution forbidden')
    if benchmark != "locomo" or plan["dataset_sha256"] != registration["source_sha256"]:
        raise ValueError("Paid v1 execution requires the frozen LoCoMo source SHA")
    expected = (registration["backbone"], registration["reader_max_tokens"], registration["concurrency"],
                registration["input_request_byte_cap"], registration["kernel_evidence_chars"],
                registration["judge_prompt_sha256"], registration.get("history_concurrency", 1))
    actual = (settings.main.model, max_tokens, concurrency, context_chars, kernel_budget_chars,
              plan["judge_prompt_sha256"], history_concurrency)
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
    frozen = {**plan, "protocol": registration["protocol"], "mode": "execution", "concurrency": concurrency,
        "scope": "full_development_retest" if len(items)==1986 else "diagnostic_subset",
        "item_ids": [item.id for item in items],
        "concurrency_scope": registration["concurrency_scope"],
        "arm_order": arm_order,
        "arms": arms, "arm_configuration": {name: ARM_CONFIGURATION[name] for name in arms},
        "history_concurrency": history_concurrency,
        "reader_max_tokens": max_tokens, "temperature": 0, "top_p": 1,
        "timeout_seconds": 120, "max_retries": 0, "thinking": "disabled",
        "kernel_budget_chars": kernel_budget_chars,
        "write_mode": "external-source-session-windows", "max_calls": max_calls,
        "max_cost_usd": max_cost_usd, "cold_warm": "provider best effort, no forced warmup",
        "main_score": "categories 1-4; category 5 is supplementary",
        "interpretation": ("source_only versus memoria holds Reader/gate/support and evidence caps fixed; "
            "rrf_only also removes adaptive gate/supplement/support; raw_context has full history. "
            "Equal caps do not imply equal actual tokens, calls, cache exposure or lifecycle cost"),
        "source_time": "raw session clock retained; dataset timezone unknown",
        "resume": "not supported; checkpoints retained, never silently recomputed as free calls"}
    recorder = RunRecorder(Path(output) if output else workspace.path("experiments", registration["protocol"]),
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

    def make_system(name, signature=None):
        if name == "raw_context":
            return RawContextSystem(client, system=READER_SYSTEM,
                max_tokens=max_tokens, budget_chars=context_chars)
        config = ARM_CONFIGURATION[name]
        directory = recorder.path / "kernel" / name
        if signature:
            directory /= signature
        return KernelMemorySystem(factory, name=name, max_tokens=max_tokens,
            budget_chars=kernel_budget_chars, directory=directory,
            form_memory=config["formation"], include_formed_memory=config["include_formed_memory"],
            adaptive=config["adaptive_gate_supplement_support"])

    systems = {name: make_system(name) for name in arm_order}
    completed = {name: 0 for name in systems}
    def checkpoint(result):
        recorder.append("cases.jsonl", result.to_dict())
        completed[result.arm] += 1
        print(json.dumps({"event": "case", "arm": result.arm, "completed": completed[result.arm],
            "planned": len(items), "id": result.id, "correct": result.correct, "error": result.error,
            "calls": len(client.rows), "retained_peak_usd": round(client.reserved_usd, 6)}, ensure_ascii=False), flush=True)
    try:
        grader = LlmJudge(client, system=JUDGE_SYSTEM_PROMPT)
        if history_concurrency > 1:
            if concurrency != 1:
                raise ValueError("History-parallel execution requires strictly ordered arms")
            factories = {name: (lambda signature, name=name: make_system(name, signature)) for name in arm_order}
            results = await run_ordered_histories(factories, items, concurrency=history_concurrency,
                judge=grader, checkpoint=checkpoint, stop_reason=lambda: client.halt_reason)
        else:
            results = await run_arms(systems, items, benchmark=benchmark, concurrency=concurrency,
                judge=grader, checkpoint=checkpoint, stop_reason=lambda: client.halt_reason)
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
        for system in systems.values():
            if close := getattr(system, "close", None):
                close()


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
    parser.add_argument("--arms", nargs="+", choices=list(ARM_CONFIGURATION),
        help="Exact registered arms; defaults to memoria and raw_context")
    parser.add_argument("--output")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--registration")
    parser.add_argument("--history-concurrency", type=int, default=1)
    parser.add_argument("--item-ids-file", help="Exact registered diagnostic item IDs; never selected by gold or success")
    parser.add_argument("--include-captions", action="store_true",
        help="Explicit LoCoMo input change: separate dataset BLIP descriptions, never speaker quotations")
    args = parser.parse_args(argv)
    result = asyncio.run(run(HistoryWorkspace(directory_for(args.user)), Settings.load(),
                             **{k: v for k, v in vars(args).items() if k != "user"}))
    print(json.dumps(result if not args.execute else result["summaries"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
