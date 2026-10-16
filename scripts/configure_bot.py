"""Private local credential entry for bot.toml; never prints IDs, secrets or tokens."""
from __future__ import annotations
import argparse
import getpass
import json
import re
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from memoria.config import Settings
from memoria.config_store import update_document
from memoria.private_files import load_private_json,save_private_json
from user_paths import directory_for


def save_bot(settings,channel,private):
    if channel not in {'feishu','qq','telegram'}:raise ValueError('Unknown channel')
    if channel=='telegram':
        if not re.fullmatch(r'\d{5,}:[A-Za-z0-9_-]{20,}',private.get('token','')):raise ValueError('Invalid Telegram token format')
        private={'token':private['token']}
    else:
        identity,secret=private.get('app_id',''),private.get('app_secret','')
        valid=bool(re.fullmatch(r'\d{6,20}',identity)) if channel=='qq' else identity.startswith('cli_')
        if not valid or not isinstance(secret,str) or len(secret)<8:raise ValueError('Invalid bot credential format')
        private={'app_id':identity,'app_secret':secret}
    table=settings.bot_settings.get(channel,{})
    target=Path(table.get('credentials_file',str(settings.data_root/'secrets'/(channel+'-bot.credentials.dpapi'))))
    if not target.is_absolute():target=settings.root/target
    save_private_json(target,private)
    def enable(document):
        row=document.setdefault(channel,{})
        row.update(enabled=True,credentials_file=str(target))
    update_document(settings.bot_config_file,enable)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel',required=True,choices=['feishu','qq','telegram'])
    parser.add_argument('--config')
    parser.add_argument('--credentials-file',type=Path,help='Private JSON input, never a tracked file')
    parser.add_argument('--from-user',help='Feishu only: reuse this account app credentials, not its OAuth')
    parser.add_argument('--disable',action='store_true')
    args=parser.parse_args();settings=Settings.load(args.config)
    if args.disable:
        update_document(settings.bot_config_file,lambda d:d.setdefault(args.channel,{}).update(enabled=False))
        print('Channel disabled in bot.toml; restart backend. Existing conversations and bindings retained.');return
    if args.from_user:
        if args.channel!='feishu':parser.error('--from-user applies only to Feishu')
        private=load_private_json(directory_for(args.from_user,args.config)/'secrets/feishu.credentials.dpapi')
    elif args.credentials_file:private=json.loads(args.credentials_file.read_text(encoding='utf-8-sig'))
    elif args.channel=='telegram':private={'token':getpass.getpass('Telegram token (hidden): ').strip()}
    else:private={'app_id':getpass.getpass('App ID (hidden): ').strip(),'app_secret':getpass.getpass('App Secret (hidden): ').strip()}
    save_bot(settings,args.channel,private)
    print('Encrypted credentials saved; bot.toml updated. No credential values printed. Restart backend and bind from Chat.')


if __name__=='__main__':main()
