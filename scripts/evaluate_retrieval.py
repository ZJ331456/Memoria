"""Run registered automatic source comparisons; credentials stay in user profiles."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from memoria.config import Settings
from memoria.experiments.history import HistoryWorkspace,read_json,write_json
from memoria.experiments.records import code_identity,digest_file
from memoria.experiments.roles import load_roles
from memoria.experiments.runner import account_settings,execute,score
from memoria.memory.embedding import EmbeddingClient
from memoria.experiments.retrieval import BASELINES
from scripts.research import private_input
from scripts.user_paths import directory_for


async def evaluate(workspace,config,registration,*,allow_remote):
    registered = read_json(registration)
    if not allow_remote:
        raise ValueError("Registered reader calls require explicit --allow-remote")
    identity = code_identity(ROOT)
    if identity["dirty"]:
        raise ValueError("Registered evaluation requires committed clean code")
    protocol,inputs,_ = workspace.protocol(registered["protocol_id"])
    for key in ("input_sha256","gold_sha256","history_sha256"):
        if protocol[key]!=registered[key]:
            raise ValueError("Registration differs from frozen source protocol")
    if len(inputs)!=registered["expected_cases"] or registered["concurrency"]!=1 or registered["rrf_k"]!=60:
        raise ValueError("Registration denominator or retrieval policy differs")
    if registered["planned_reader_calls"]!=len(inputs)*len(registered["qa_order"]):
        raise ValueError("Registered reader call denominator differs from ordered workload")
    if any(baseline not in BASELINES for mode in ("qa","retrieval") for baseline in registered[mode+"_order"]):
        raise ValueError("Registration contains an unsupported baseline")
    if registered["similarity_threshold"] is not None:
        raise ValueError("This implementation does not use similarity cutoffs")
    settings = account_settings(Settings.load(config),workspace,registered["profile"])
    if settings.main.model!=registered["reader_model"]:
        raise ValueError("Actual reader model differs from registration")
    from memoria.config import ModelConfig
    embedding = EmbeddingClient(ModelConfig("model/bge-small-zh-v1.5","","local://"+registered["embedding_device"]))
    if embedding.namespace!=registered["embedding_namespace"]:
        raise ValueError("Local BGE weights/tokenizer differ from registration")
    roles = load_roles(private_input(ROOT/registered["roles"],workspace.directory))
    runs = []
    summary = workspace.path("reports",registered["evaluation_id"]+".json")
    summary.parent.mkdir(exist_ok=True,parents=True,mode=0o700)
    if summary.exists():
        raise ValueError("Evaluation report exists; register a new evaluation ID to rerun")
    def save(status):
        write_json(summary,{"evaluation_id":registered["evaluation_id"],"registration_sha256":digest_file(registration),
                            "code":identity,"status":status,"runs":runs,
                            "planned_reader_calls":registered["planned_reader_calls"],"human_evaluation":"deferred by user"})
    save("running")
    try:
        for mode in ("retrieval","qa"):
            for index,baseline in enumerate(registered[mode+"_order"]):
                current = code_identity(ROOT)
                if current["dirty"] or current["commit"]!=identity["commit"]:
                    raise ValueError("Code changed during registered evaluation")
                print(json.dumps({"event":"started","mode":mode,"baseline":baseline,"index":index}),flush=True)
                output = await execute(workspace,Settings.load(config),mode=mode,protocol_id=registered["protocol_id"],
                    baseline=baseline,history_limit=registered["history_limit"],context_chars=registered["context_chars"],
                    embedding_device=registered["embedding_device"] if baseline in {"B2_dense","B2_rrf"} else None,
                    roles=roles,profile=registered["profile"] if mode=="qa" else None,allow_remote=allow_remote)
                scored = score(workspace,output.name)
                manifest = read_json(output/"manifest.json")
                metrics = read_json(output/"metrics.json")
                runs.append({"mode":mode,"baseline":baseline,"index":index,"run_id":output.name,
                             "manifest_sha256":digest_file(output/"manifest.json"),"score_sha256":digest_file(scored),
                             "score_file":scored.relative_to(workspace.directory).as_posix(),
                             "calls":metrics["calls"],"statuses":metrics["statuses"],"elapsed_ms":manifest["elapsed_ms"]})
                save("running")
                print(json.dumps({"event":"completed","mode":mode,"baseline":baseline,"run_id":output.name,
                                  "calls":metrics["calls"],"statuses":metrics["statuses"]}),flush=True)
        actual_calls = sum(run["calls"] for run in runs)
        if actual_calls!=registered["planned_reader_calls"]:
            raise ValueError("Actual reader call count differs; keep failed/not-attempted denominators")
        save("completed_with_case_errors" if any(run["statuses"].get("error",0) for run in runs) else "completed")
    except BaseException:
        save("interrupted_or_failed")
        raise
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user",required=True)
    parser.add_argument("--config")
    parser.add_argument("--registration",default="configs/research-retrieval-eval-20261012.json")
    parser.add_argument("--allow-remote",action="store_true")
    args = parser.parse_args()
    workspace = HistoryWorkspace(directory_for(args.user,args.config))
    registration = private_input(args.registration,workspace.directory)
    print(asyncio.run(evaluate(workspace,args.config,registration,allow_remote=args.allow_remote)))


if __name__=="__main__":
    try:
        main()
    except (ValueError,FileNotFoundError) as error:
        print("Evaluation rejected: "+str(error),file=sys.stderr)
        raise SystemExit(2)
