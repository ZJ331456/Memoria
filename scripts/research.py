"""Private local CLI for real history, human annotation and reproducible evaluation."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from memoria.config import Settings
from memoria.experiments.history import HistoryWorkspace, identifier, write_json
from memoria.experiments.roles import load_roles
from memoria.experiments.runner import activate_profile, execute, recover_run, score, score_template
from memoria.experiments.access_eval import benchmark_access
from memoria.experiments.evidence_eval import replay_selection
from memoria.experiments.retrieval import BASELINES
from memoria.experiments.locomo import execute_smoke
from memoria.experiments.locomo_eval import execute as execute_locomo
from scripts.user_paths import directory_for


def private_input(path, directory):
    source = Path(path).resolve(strict=True)
    data_root = directory.parent.parent.resolve()
    users = data_root / "users"
    if source.is_relative_to(users) and not source.is_relative_to(directory.resolve()):
        raise ValueError("Cannot read another account's private research input")
    if source.is_relative_to(data_root / "legacy"):
        raise ValueError("Old personal archives are excluded from this research workflow")
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True, help="Existing account; local administrator CLI")
    parser.add_argument("--config")
    subs = parser.add_subparsers(dest="command", required=True)
    sub = subs.add_parser("import-history")
    sub.add_argument("--input", required=True, help="Real consented history JSON; see documentation schema")
    sub = subs.add_parser("use-public")
    sub.add_argument("--dataset", default="lccc-base-validation-multiturn-50")
    sub = subs.add_parser("annotation-template")
    sub.add_argument("--dataset", required=True)
    sub.add_argument("--reviewer", required=True)
    sub = subs.add_parser("submit-annotation")
    sub.add_argument("--input", required=True)
    sub = subs.add_parser("freeze")
    sub.add_argument("--dataset", required=True)
    sub.add_argument("--protocol", required=True)
    sub.add_argument("--reviewers", nargs="+", required=True)
    sub.add_argument("--adjudication")
    sub = subs.add_parser("run")
    sub.add_argument("--mode", choices=["extract", "qa", "retrieval"], required=True)
    sub.add_argument("--dataset")
    sub.add_argument("--protocol")
    sub.add_argument("--limit", type=int, default=50, help="Extraction only; QA uses every frozen probe")
    sub.add_argument("--baseline", choices=sorted(BASELINES), default="B0_recent")
    sub.add_argument("--history-limit", type=int, default=8)
    sub.add_argument("--context-chars", type=int, default=12000)
    sub.add_argument("--evidence-selector", choices=["legacy","topk","coverage","mmr","evidence_coverage"], default="legacy")
    sub.add_argument("--evidence-budget-unit", choices=["chars","utf8_bytes"], default="chars",
                     help="Evidence JSON budget unit; remote reader tokens are not guessed")
    sub.add_argument("--embedding-device", choices=["auto","cuda","cpu"],help="Run-local BGE override; does not change production model settings")
    sub.add_argument("--roles")
    sub.add_argument("--allow-remote", action="store_true")
    sub.add_argument("--profile", help="Private encrypted research model profile")
    sub.add_argument("--extraction-protocol", choices=["production_turn", "source_grounded"], default="production_turn")
    sub = subs.add_parser("benchmark-access")
    sub.add_argument("--repeats",type=int,default=3)
    sub.add_argument("--iterations",type=int,default=50)
    sub = subs.add_parser("locomo-smoke", help="Fixed ten-question official LoCoMo execution test; no production writes")
    sub.add_argument("--input", required=True, help="Verified official locomo10.json")
    sub.add_argument("--profile")
    sub.add_argument("--roles")
    sub.add_argument("--allow-remote", action="store_true")
    sub.add_argument("--embedding-device", choices=["auto", "cuda", "cpu"], default="auto")
    sub.add_argument("--baseline", choices=["B0_recent", "B2_fts_compact", "B2_dense", "B2_rrf"], default="B2_rrf")
    sub = subs.add_parser("locomo-run", help="English v2 benchmark; all official QA by default; no production writes")
    sub.add_argument("--input", required=True)
    sub.add_argument("--scope", choices=["smoke", "full"], default="full")
    sub.add_argument("--profile")
    sub.add_argument("--roles")
    sub.add_argument("--allow-remote", action="store_true")
    sub.add_argument("--embedding-device", choices=["auto", "cuda", "cpu"], default="auto")
    sub.add_argument("--concurrency", type=int, default=4)
    sub.add_argument("--embedding-model", choices=["bge-small-en-v1.5", "bge-small-zh-v1.5"], default="bge-small-en-v1.5")
    sub.add_argument("--fallback", choices=["none", "full_history"], default="full_history")
    sub.add_argument("--resume-from", help="Stopped fingerprinted checkpoint in the same account; preserve completed cases")
    sub.add_argument("--retry-errors", action="store_true", help="With --resume-from, retry failed cases; keep all original failures in source run")
    sub = subs.add_parser("select-evidence",help="Compare four selectors on a captured retrieval pool; zero model calls")
    sub.add_argument("--source-run",required=True)
    sub.add_argument("--budget",type=int,default=1024)
    sub.add_argument("--unit",choices=["chars","utf8_bytes"],default="chars")
    for name in ("score-template", "score"):
        sub = subs.add_parser(name)
        sub.add_argument("--run", required=True)
        if name == "score":
            sub.add_argument("--judgments")
    sub = subs.add_parser("recover-run")
    sub.add_argument("--run", required=True)
    sub.add_argument("--confirm-stopped", action="store_true")
    sub = subs.add_parser("activate-model")
    sub.add_argument("--profile", required=True)
    sub.add_argument("--validated-run", required=True)
    args = parser.parse_args()
    directory = directory_for(args.user, args.config)
    workspace = HistoryWorkspace(directory)
    if args.command == "import-history":
        result = workspace.import_history(private_input(args.input, directory))
    elif args.command == "use-public":
        result = workspace.import_public(args.dataset)
    elif args.command == "annotation-template":
        output = workspace.path("research", "drafts", identifier(args.dataset) + "." + identifier(args.reviewer) + ".json")
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if output.exists():
            raise ValueError("Draft exists; choose a new reviewer revision ID")
        write_json(output, workspace.template(args.dataset, args.reviewer))
        result = output
    elif args.command == "submit-annotation":
        workspace.submit(private_input(args.input, directory))
        result = "Annotation and declared provenance validated and saved (immutable)"
    elif args.command == "freeze":
        result = workspace.freeze(args.dataset, args.protocol, args.reviewers,
                                  private_input(args.adjudication, directory) if args.adjudication else None)
    elif args.command == "run":
        if (args.mode == "extract" and not args.dataset) or (args.mode in {"qa","retrieval"} and not args.protocol):
            parser.error("extract requires --dataset; qa requires --protocol")
        result = asyncio.run(execute(workspace, Settings.load(args.config), mode=args.mode,
                                    dataset=args.dataset, protocol_id=args.protocol, limit=args.limit,
                                    baseline=args.baseline, history_limit=args.history_limit, context_chars=args.context_chars,
                                    evidence_selector=args.evidence_selector, evidence_budget_unit=args.evidence_budget_unit,
                                    embedding_device=args.embedding_device,
                                    allow_remote=args.allow_remote,
                                    profile=args.profile, extraction_protocol=args.extraction_protocol,
                                    roles=load_roles(private_input(args.roles, directory) if args.roles else None)))
    elif args.command == "locomo-smoke":
        result = asyncio.run(execute_smoke(workspace, Settings.load(args.config), private_input(args.input, directory),
            profile=args.profile, allow_remote=args.allow_remote, baseline=args.baseline,
            embedding_device=args.embedding_device,
            roles=load_roles(private_input(args.roles, directory) if args.roles else None)))
    elif args.command == "locomo-run":
        result = asyncio.run(execute_locomo(workspace, Settings.load(args.config), private_input(args.input, directory),
            scope=args.scope, profile=args.profile, allow_remote=args.allow_remote,
            embedding_device=args.embedding_device, concurrency=args.concurrency,
            embedding_model=args.embedding_model,
            fallback=args.fallback,
            resume_from=args.resume_from,
            retry_errors=args.retry_errors,
            roles=load_roles(private_input(args.roles, directory) if args.roles else None)))
    elif args.command == "benchmark-access":
        result = benchmark_access(workspace,Settings.load(args.config),repeats=args.repeats,iterations=args.iterations)
    elif args.command == "select-evidence":
        result = replay_selection(workspace,args.source_run,budget=args.budget,unit=args.unit,root=ROOT)
    elif args.command == "score-template":
        output = workspace.path("research", "drafts", identifier(args.run) + ".scores.json")
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if output.exists():
            raise ValueError("Scoring draft exists")
        write_json(output, score_template(workspace, args.run))
        result = output
    elif args.command == "recover-run":
        result = recover_run(workspace, args.run, confirm_stopped=args.confirm_stopped)
    elif args.command == "activate-model":
        result = activate_profile(workspace, Settings.load(args.config), args.profile, args.validated_run)
    else:
        result = score(workspace, args.run, private_input(args.judgments, directory) if args.judgments else None)
    print(result)  # No raw dialogue, answer, password or provider credential on stdout.


if __name__ == "__main__":
    try:
        main()
    except (ValueError, FileNotFoundError) as exc:
        print(f"Research command rejected: {exc}", file=sys.stderr)
        raise SystemExit(2)
