"""Read-only bot authentication/endpoint checks; sends no messages and calls no LLM."""
import argparse
import asyncio
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from memoria.config import Settings
from memoria.private_files import load_private_json
from memoria.channels.errors import ChannelError
from memoria.channels.telegram import TelegramAPI
from memoria.channels.qq import QQAPI
from memoria.channels.feishu_api import FeishuAPI


async def check(settings,channel):
    row={'channel':channel,'authenticated':False,'endpoint_verified':False,'messages_sent':0,'llm_calls':0}
    api=None
    try:
        table=settings.bot_settings.get(channel,{})
        path=Path(table['credentials_file'])
        if not path.is_absolute():path=settings.root/path
        private=load_private_json(path)
        if channel=='telegram':
            api=TelegramAPI(private,proxy=table.get('proxy_url'))
            me=await api.verify();row['authenticated']=True
            expected=table.get('expected_username','')
            if expected and me['username'].lower()!=expected.lstrip('@').lower():raise ChannelError('telegram_username_mismatch')
        elif channel=='qq':
            api=QQAPI(private,proxy=table.get('proxy_url'))
            await api.authenticate();row['authenticated']=True
            await api.gateway()
        else:
            api=FeishuAPI(private);await api.authenticate();row['authenticated']=True
        row['endpoint_verified']=True
    except Exception as exc:row['error']=exc.code if isinstance(exc,ChannelError) else 'private_configuration_or_network_error'
    finally:
        if api:await api.close()
    return row


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel',choices=['feishu','qq','telegram'])
    parser.add_argument('--config')
    args=parser.parse_args();settings=Settings.load(args.config)
    names=[args.channel] if args.channel else [name for name in ('feishu','qq','telegram') if settings.bot_settings.get(name,{}).get('enabled')]
    results=await asyncio.gather(*(check(settings,name) for name in names))
    print(json.dumps(results,ensure_ascii=False,indent=2))
    if any(not row['endpoint_verified'] for row in results):raise SystemExit(1)


if __name__=='__main__':asyncio.run(main())
