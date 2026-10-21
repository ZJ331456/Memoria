"""Validate or execute the explicitly synthetic, isolated core-memory acceptance suite."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from memoria.config import Settings
from memoria.experiments.history import HistoryWorkspace
from memoria.experiments.simulation import run_suite,offline_retrieval,summarize_runs
from scripts.user_paths import directory_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', required=True, help='Existing account for its configured model; no production-history access')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--allow-remote', action='store_true')
    parser.add_argument('--max-calls', type=int, default=144)
    parser.add_argument('--max-input-chars', type=int, default=800000)
    parser.add_argument('--only', nargs='+')
    parser.add_argument('--direct-http',action='store_true',help='Bypass system proxy for configured LLM hosts in this process only; TLS verification remains enabled')
    parser.add_argument('--offline-retrieval',action='store_true',help='Real local BGE engine diagnostic; use --execute, no remote LLM calls')
    parser.add_argument('--summarize',action='store_true',help='Verify frozen runs and write a cross-version functional checklist; no models')
    args = parser.parse_args()
    settings = Settings.load()
    workspace = HistoryWorkspace(directory_for(args.user))
    if args.summarize:
        print(json.dumps(summarize_runs(settings.root),ensure_ascii=False));return
    if args.offline_retrieval:
        result=asyncio.run(offline_retrieval(workspace,settings)) if args.execute else {'status':'validated_not_executed','model_calls':0}
        print(json.dumps(result,ensure_ascii=False));return 0 if not args.execute or result.get('passed') else 1
    previous={k:os.environ.get(k) for k in ('NO_PROXY','no_proxy')}
    hosts=[]
    try:
        if args.direct_http:
            from urllib.parse import urlsplit
            from memoria.models_config import apply_account_settings
            selected=copy.deepcopy(settings);apply_account_settings(selected,workspace.directory)
            hosts=sorted({urlsplit(m.base_url).hostname for m in (selected.main,selected.fast) if m.base_url.startswith('https://')})
            bypass=','.join(filter(None,[previous['NO_PROXY'] or previous['no_proxy'] or '',*hosts]))
            os.environ['NO_PROXY']=os.environ['no_proxy']=bypass
        result = asyncio.run(run_suite(workspace, settings, execute=args.execute, allow_remote=args.allow_remote,
            max_calls=args.max_calls, max_input_chars=args.max_input_chars, only=args.only,
            network_profile={'mode':'process_direct','hosts':hosts} if args.direct_http else 'environment'))
    finally:
        for key,value in previous.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value
    print(json.dumps(result, ensure_ascii=False))
    if args.execute:
        return 0 if (result.get('acceptance_passed') if args.only is None else
            result.get('question_outcomes')=={'passed':result.get('questions')} and not result.get('blocked_calls')) else 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
