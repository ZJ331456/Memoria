"""Validate or run isolated, paired entity retrieval diagnostics; defaults to zero calls."""
import argparse
import asyncio
import copy
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from memoria.config import Settings
from memoria.experiments.entity_retrieval import load_inputs,run_comparison,METHODS
from memoria.models_config import apply_account_settings
from scripts.user_paths import directory_for


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--user',required=True,help='Only select model configuration; never read production history')
    p.add_argument('--execute',action='store_true')
    p.add_argument('--reader-cases',nargs='*',default=[])
    p.add_argument('--allow-remote',action='store_true')
    p.add_argument('--max-calls',type=int,default=0)
    p.add_argument('--budget-chars',type=int,default=8000)
    p.add_argument('--direct-http',action='store_true')
    args=p.parse_args();suite=load_inputs(ROOT)
    if not args.execute:
        print(json.dumps({'status':'validated_not_executed','cases':len(suite['cases']),'methods':METHODS,'model_calls':0}));return 0
    settings=apply_account_settings(copy.deepcopy(Settings.load()),directory_for(args.user))
    previous={k:os.environ.get(k) for k in ('NO_PROXY','no_proxy')}
    try:
        if args.direct_http:
            hosts=sorted({urlsplit(m.base_url).hostname for m in (settings.main,settings.fast) if m.base_url.startswith('https://')})
            os.environ['NO_PROXY']=os.environ['no_proxy']=','.join(filter(None,[previous['NO_PROXY'] or previous['no_proxy'] or '',*hosts]))
        result=asyncio.run(run_comparison(settings,reader_cases=args.reader_cases,allow_remote=args.allow_remote,
            max_calls=args.max_calls,budget_chars=args.budget_chars))
        print(json.dumps(result,ensure_ascii=False));return 0 if result['passed'] else 1
    finally:
        for k,v in previous.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v


if __name__=='__main__':sys.exit(main())
