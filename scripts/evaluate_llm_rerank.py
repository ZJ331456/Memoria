"""Validate or execute the four-question fixed-pool source-Reader protocol (max 12 calls)."""
import argparse,asyncio,copy,json,os,sys
from pathlib import Path
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from memoria.config import Settings
from memoria.models_config import apply_account_settings
from memoria.experiments.rerank_comparison import inputs,run
from scripts.user_paths import directory_for

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--user',required=True)
    p.add_argument('--execute',action='store_true');p.add_argument('--allow-remote',action='store_true')
    p.add_argument('--max-calls',type=int,default=0);p.add_argument('--budget-chars',type=int,default=3600)
    p.add_argument('--direct-http',action='store_true');args=p.parse_args()
    path=ROOT/'data/benchmarks/locomo/raw/locomo10.json';frozen=inputs(path)
    if not args.execute:print(json.dumps({'status':'validated_not_executed','questions':len(frozen['questions']),'model_calls':0}));return 0
    if not args.allow_remote or args.max_calls!=12:raise ValueError('Execution requires --allow-remote --max-calls 12')
    s=apply_account_settings(copy.deepcopy(Settings.load()),directory_for(args.user))
    if args.direct_http:
        hosts={urlsplit(x.base_url).hostname for x in (s.main,s.fast)}
        os.environ['NO_PROXY']=os.environ['no_proxy']=','.join(filter(None,[os.environ.get('NO_PROXY',''),*hosts]))
    result=asyncio.run(run(s,path,max_calls=12,budget_chars=args.budget_chars));print(json.dumps(result,ensure_ascii=False))
    return int(bool(result['execution_failures']) or not result['core_unchanged'])

if __name__=='__main__':sys.exit(main())
