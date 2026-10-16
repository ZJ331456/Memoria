"""Validate the single bot.toml; secrets are encrypted files, never public fields."""
from urllib.parse import urlparse


def validate_bot_configuration(data):
    if not isinstance(data,dict) or set(data)-{'feishu','qq','telegram'}:raise ValueError('bot.toml只接受feishu/qq/telegram表')
    for name,table in data.items():
        allowed={'enabled','credentials_file','processing_notice'}
        if name in {'qq','telegram'}:allowed.add('proxy_url')
        if name=='telegram':allowed|={'expected_username','poll_timeout_seconds'}
        if not isinstance(table,dict) or set(table)-allowed:raise ValueError('bot.toml中存在无效配置字段')
        if type(table.get('enabled',False)) is not bool:raise ValueError('机器人enabled必须是布尔值')
        if type(table.get('processing_notice',False)) is not bool:raise ValueError('processing_notice必须是布尔值')
        if 'credentials_file' in table and (not isinstance(table['credentials_file'],str) or not table['credentials_file'].strip()):raise ValueError('credentials_file必须是非空路径')
        if 'expected_username' in table and not isinstance(table['expected_username'],str):raise ValueError('expected_username必须是字符串')
        timeout=table.get('poll_timeout_seconds',25)
        if type(timeout) is not int or not 1<=timeout<=30:raise ValueError('Telegram轮询超时须为1..30秒')
        proxy=table.get('proxy_url','')
        if not isinstance(proxy,str):raise ValueError('proxy_url必须是字符串')
        if proxy:
            parsed=urlparse(proxy)
            if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.query or parsed.fragment:
                raise ValueError('proxy_url只支持HTTP(S)代理地址')
