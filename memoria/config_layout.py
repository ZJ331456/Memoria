"""Explicit stopped-service migration to four centralized configuration files."""
from __future__ import annotations
import copy
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from .config import Settings, _merge, _read_with_parent
from .config_store import read_document, write_document
from .models_config import model_document, validate_model_document, apply_account_settings


def _safe(path: Path, project: Path) -> Path:
    path = path.absolute()
    if not path.resolve().is_relative_to(project.resolve()):
        raise ValueError('Configuration migration target escapes its project directory')
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('Configuration migration refuses linked paths')
        if part == project:
            break
    if path.is_dir():
        for child in path.rglob('*'):
            if child.is_symlink() or (hasattr(child, 'is_junction') and child.is_junction()):
                raise ValueError('Configuration migration refuses linked contents')
    return path


def migrate_server_configuration(source: Path) -> dict:
    source = source.absolute()
    project = source.parent.parent if source.parent.name == 'config' else source.parent
    _safe(source, project)
    directory = _safe(project / 'config', project)
    targets = {name: directory / (name + '.toml') for name in ('system', 'models', 'bot', 'mcp')}
    if source==targets['system'] and source.is_file() and not targets['bot'].exists() and (directory/'feishu.toml').is_file():
        # Upgrade the immediately previous four-file layout without touching
        # model/MCP files, account data, credentials or existing Feishu bindings.
        old=_safe(directory/'feishu.toml',project)
        previous=read_document(old)
        if set(previous)!={'bot'}:raise ValueError('Unknown old Feishu configuration tables')
        raw=_read_with_parent(source.resolve())
        database=Path(raw.get('storage',{}).get('database','data/memoria.db'))
        data_root=_safe(database.parent if database.is_absolute() else project/database.parent,project)
        if data_root==project or directory.is_relative_to(data_root) or data_root.is_relative_to(directory):raise ValueError('Data/config roots overlap')
        bot=read_document(Path(__file__).resolve().parents[1]/'config/bot.example.toml')
        bot['feishu']=previous['bot']
        from .channels.config import validate_bot_configuration
        validate_bot_configuration(bot)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup=_safe(data_root/'backups'/('bot-config-'+stamp),project)
        backup.mkdir(parents=True,exist_ok=False,mode=0o700)
        shutil.copy2(old,backup/'feishu.toml')
        try:
            write_document(targets['bot'],bot)
            Settings.load(source)
            old.unlink()
        except BaseException:
            targets['bot'].unlink(missing_ok=True)
            raise
        return {'already_migrated':False,'runtime_files':4,'bot_config':str(targets['bot']),'backup':str(backup),'account_files_changed':0}
    if source==targets['system'] and all(path.is_file() for path in targets.values()):
        Settings.load(source)
        return {'already_migrated':True,'runtime_files':4}
    if not source.exists() and all(path.is_file() for path in targets.values()):
        Settings.load(targets['system'])
        return {'already_migrated': True, 'runtime_files': 4}
    if any(path.exists() for path in targets.values()):
        raise ValueError('Destination configuration already exists; no files overwritten')
    raw = _read_with_parent(source.resolve())
    database = Path(raw.get('storage', {}).get('database', 'data/memoria.db'))
    data_root = _safe(database.parent if database.is_absolute() else project / database.parent, project)
    if data_root == project or directory.is_relative_to(data_root) or data_root.is_relative_to(directory):
        raise ValueError('Data root and configuration directory must be separate project subdirectories')
    private = Path(raw.get('configuration', {}).get('private_directory', 'configs/local'))
    private = _safe(private if private.is_absolute() else source.parent / private, project)
    if private in {project, directory, data_root} or directory.is_relative_to(private) or private.is_relative_to(data_root / 'users'):
        raise ValueError('Legacy private directory overlaps project, data or account storage')
    model_path = raw.get('models', {}).get('file')
    models = model_document(raw)
    originals = [source]
    if model_path:
        path = Path(model_path)
        path = _safe(path if path.is_absolute() else source.parent / path, project)
        models = _merge(models, read_document(path))
        originals.append(path)
    old_data_config = _safe(data_root / 'config', project)
    for old in (old_data_config / 'models.override.toml',):
        if old.exists():
            models = _merge(models, read_document(old)); originals.append(old)
    models.get('llm', {}).pop('vl', None)  # No runtime consumer exists.
    validate_model_document(models)
    system = copy.deepcopy(raw)
    for key in ('models', 'configuration'):
        system.pop(key, None)
    for parent, names in (('llm', ('main', 'fast', 'vl', 'provider')), ('memory', ('embedding',))):
        for name in names:
            system.get(parent, {}).pop(name, None)
    system.get('agent', {}).get('mcp', {}).pop('config_file', None)
    for unused in ('enabled', 'engine'):
        system.get('memory', {}).pop(unused, None)
    template = Path(__file__).resolve().parents[1] / 'config/mcp.example.toml'
    mcp = read_document(template)
    feishu = {'feishu': {'enabled': False, 'credentials_file': 'data/secrets/feishu-bot.credentials.dpapi'}, 'qq': {'enabled':False}, 'telegram': {'enabled':False}}
    bot_directory = private if (private / 'feishu-bot.json').exists() else old_data_config
    if (bot_directory / 'feishu-bot.json').exists():
        old = bot_directory / 'feishu-bot.json'
        feishu['feishu']['enabled'] = json.loads(old.read_text(encoding='utf-8-sig')).get('enabled') is True
        originals.append(old)
    bot_source = bot_directory / 'feishu-bot.credentials.dpapi'
    bot_target = _safe(data_root / 'secrets/feishu-bot.credentials.dpapi', project)
    feishu['feishu']['credentials_file'] = bot_target.relative_to(project).as_posix()
    if bot_source.exists():
        originals.append(bot_source)
        if bot_target.exists():
            raise ValueError('Bot credential destination already exists')
    def portable(value, account):
        if isinstance(value, str):
            return value.replace(str(account), '{account_dir}').replace(account.as_posix(), '{account_dir}').replace(str(project), '{project}').replace(project.as_posix(), '{project}').replace('\\', '/')
        if isinstance(value, list):return [portable(v, account) for v in value]
        if isinstance(value, dict):return {k:portable(v, account) for k,v in value.items()}
        return value
    accounts = 0
    for account in sorted((data_root / 'users').glob('*')):
        if not account.is_dir():continue
        import re
        if not re.fullmatch(r'[a-f0-9]{32}', account.name):
            raise ValueError('Unexpected account directory; inspect before migration')
        _safe(account, project)
        old_models = account / 'config/models.override.toml'
        if old_models.is_file():
            payload = read_document(old_models);validate_model_document(payload)
            models.setdefault('accounts', {})[account.name] = payload
            originals.append(old_models);accounts += 1
        old_mcp = account / 'config/mcp_servers.json'
        if old_mcp.is_file():
            payload = json.loads(old_mcp.read_text(encoding='utf-8'))
            from .mcp.config import parse_mcp_servers
            parse_mcp_servers(payload)
            enabled, overrides = [], {}
            for server in payload['servers']:
                server = portable(server, account)
                name = server.pop('name')
                if server.pop('enabled', True):enabled.append(name)
                base = mcp['servers'].get(name, {})
                difference = {key:value for key,value in server.items() if base.get(key) != value}
                if difference:overrides[name] = difference
            selection = {'enabled_servers': enabled}
            if overrides:selection['servers'] = overrides
            mcp.setdefault('accounts', {})[account.name] = {'mcp': selection}
            originals.append(old_mcp)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = _safe(data_root / 'backups' / ('configuration-' + stamp), project)
    backup.mkdir(parents=True, exist_ok=False, mode=0o700)
    # Backup known inputs before publication. Encrypted credentials stay encrypted.
    for path in set(originals):
        _safe(path, project)
        target = backup / 'originals' / path.relative_to(project)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    created, moved = [], []
    try:
        if bot_source.exists():
            bot_target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(bot_source, bot_target);created.append(bot_target)
        for name, document in (('system', system), ('models', models), ('bot', feishu), ('mcp', mcp)):
            write_document(targets[name], document);created.append(targets[name])
        checked = Settings.load(targets['system'])
        for owner in models.get('accounts', {}):
            apply_account_settings(copy.deepcopy(checked), data_root / 'users' / owner)
        from .mcp.config import configured_mcp_servers
        for owner in mcp.get('accounts', {}):
            configured_mcp_servers(apply_account_settings(copy.deepcopy(checked), data_root / 'users' / owner))
        # Retire known active files only. Preserve all older backup material.
        for path in set(originals):
            destination = backup / 'retired' / path.relative_to(project)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination));moved.append((path,destination))
        for old_directory in (private, old_data_config):
            if old_directory.is_dir():
                if old_directory not in {project / 'configs/local', data_root / 'config'}:
                    continue  # Custom directories may contain unrelated files.
                _safe(old_directory, project)
                destination = backup / 'retired-directories' / old_directory.relative_to(project)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_directory), str(destination));moved.append((old_directory,destination))
    except BaseException:
        for start, destination in reversed(moved):
            start.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(start))
        for path in reversed(created):path.unlink(missing_ok=True)
        raise
    for path in originals:
        if path.parent.is_dir() and path.parent.name == 'config' and path.parent != directory and not any(path.parent.iterdir()):path.parent.rmdir()
    return {'already_migrated': False, 'runtime_files': 4, 'account_model_sections': accounts,
            'backup': str(backup), 'system_config': str(targets['system']), 'credentials_decrypted_for_copy': False}
