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
    sub.add_argument("--mode", choices=["extract", "qa"], required=True)
    sub.add_argument("--dataset")
    sub.add_argument("--protocol")
    sub.add_argument("--limit", type=int, default=50, help="Extraction only; QA uses every frozen probe")
    sub.add_argument("--baseline", choices=["B0_recent", "B2_fts"], default="B0_recent")
    sub.add_argument("--history-limit", type=int, default=8)
    sub.add_argument("--context-chars", type=int, default=12000)
    sub.add_argument("--roles")
    sub.add_argument("--allow-remote", action="store_true")
    sub.add_argument("--profile", help="Private encrypted research model profile")
    sub.add_argument("--extraction-protocol", choices=["production_turn", "source_grounded"], default="production_turn")
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
        if (args.mode == "extract" and not args.dataset) or (args.mode == "qa" and not args.protocol):
            parser.error("extract requires --dataset; qa requires --protocol")
        result = asyncio.run(execute(workspace, Settings.load(args.config), mode=args.mode,
                                    dataset=args.dataset, protocol_id=args.protocol, limit=args.limit,
                                    baseline=args.baseline, history_limit=args.history_limit, context_chars=args.context_chars,
                                    allow_remote=args.allow_remote,
                                    profile=args.profile, extraction_protocol=args.extraction_protocol,
                                    roles=load_roles(private_input(args.roles, directory) if args.roles else None)))
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
