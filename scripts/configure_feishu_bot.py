"""Configure bot ingress locally; independent of MCP OAuth and user bindings."""
from __future__ import annotations
import argparse
import getpass
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from memoria.config import Settings
from memoria.private_files import load_private_json,save_private_json
from user_paths import directory_for


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config')
    parser.add_argument('--from-user',help='Reuse this account\'s existing encrypted MCP app credentials, not its OAuth token')
    parser.add_argument('--credentials-file',type=Path,help='Private input JSON, never a tracked project file')
    parser.add_argument('--disable',action='store_true')
    args=parser.parse_args()
    root=Settings.load(args.config).database.parent.resolve()
    target=root/'config/feishu-bot.json';target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if args.disable:
        target.write_text('{"enabled": false}\n',encoding='utf-8');print('Bot ingress disabled; restart the backend. Existing data and bindings are retained.');return
    if args.from_user:
        private=load_private_json(directory_for(args.from_user,args.config)/'secrets/feishu.credentials.dpapi')
    elif args.credentials_file:
        private=json.loads(args.credentials_file.read_text(encoding='utf-8-sig'))
    else:
        private={'app_id':input('App ID (private local input): ').strip(),'app_secret':getpass.getpass('App Secret (hidden): ').strip()}
    if not isinstance(private.get('app_id'),str) or not private['app_id'].startswith('cli_') or not private.get('app_secret'):
        raise SystemExit('Invalid private bot credentials; no value was printed')
    save_private_json(root/'config/feishu-bot.credentials.dpapi',{'app_id':private['app_id'],'app_secret':private['app_secret']})
    target.write_text('{"enabled": true}\n',encoding='utf-8')
    print('Bot credentials saved with Windows DPAPI. No ID/secret printed. Restart backend; configure receive-message events and pair from Chat.')

if __name__=='__main__':main()
