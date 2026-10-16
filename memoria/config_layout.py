"""Explicit, local-only migration of server configuration; never moves account data."""
from __future__ import annotations

import copy
import os
import re
import shutil
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings, _read_with_parent
from .models_config import SLOT_PATHS, apply_overrides, load_model_overrides, write_model_file


def _safe(path: Path, parent: Path) -> Path:
    absolute = path.absolute()
    if not absolute.resolve().is_relative_to(parent.resolve()):
        raise ValueError('Configuration migration target escapes its project directory')
    for part in (absolute, *absolute.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('Configuration migration refuses linked paths')
        if part == parent:
            break
    if absolute.is_dir():
        for child in absolute.rglob('*'):
            if child.is_symlink() or (hasattr(child, 'is_junction') and child.is_junction()):
                raise ValueError('Configuration migration refuses linked contents')
    return absolute


def _remove_model_tables(text: str) -> str:
    """Preserve non-model settings/comments, including arrays and multiline prompts."""
    output, skip, table = [], False, ''
    for line in text.splitlines(keepends=True):
        header = re.match(r'^\s*\[([^\[\]]+)\]\s*(?:#.*)?$', line.strip())
        if header:
            table = header.group(1).strip()
            skip = table in {'llm.main', 'llm.fast', 'llm.vl', 'memory.embedding', 'models', 'configuration'}
        if table == 'llm' and re.match(r'^\s*provider\s*=', line):
            continue  # The old global provider selector was never consumed.
        if not skip:
            output.append(line)
    return ''.join(output).rstrip() + '\n'


def _replace_text(path: Path, text: str | bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('wb', dir=path.parent, prefix='.config-', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text.encode('utf-8') if isinstance(text, str) else text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def migrate_server_configuration(source: Path) -> dict:
    source = source.absolute()
    project = source.parent
    _safe(source, project)
    original = source.read_bytes()
    text = original.decode('utf-8-sig')
    settings = Settings.load(source, _legacy_migration=True)
    directory = _safe(settings.private_config_directory, project)
    legacy = _safe(settings.database.parent / 'config', project)
    target = _safe(directory / 'models.toml', project)
    data = _read_with_parent(source.resolve())
    if target.exists():
        if settings.model_override_file == target and not legacy.exists():
            return {'already_migrated': True, 'account_files_changed': 0}
        raise ValueError('Destination models.toml already exists; no file overwritten')
    allowed = {'models.override.toml', 'feishu-bot.json', 'feishu-bot.credentials.dpapi', 'backups'}
    if legacy.is_dir() and any(p.name not in allowed for p in legacy.iterdir()):
        raise ValueError('Unknown legacy config entries; inspect locally before migration')
    legacy_models = load_model_overrides(legacy / 'models.override.toml')
    apply_overrides(settings, legacy_models)
    models = {}
    for slot, keys in SLOT_PATHS.items():
        config = getattr(settings, slot)
        raw = data
        old = legacy_models
        for key in keys:
            raw = raw.get(key, {})
            old = old.get(key, {})
        # Preserve env expressions instead of materializing their current secrets.
        current_file = load_model_overrides(settings.model_override_file)
        for key in keys:
            current_file = current_file.get(key, {})
        credentials = {**raw, **current_file, **old}
        section = {'backend': config.backend_kind('embedding' if slot == 'embedding' else 'llm') if config.base_url else 'auto', 'model': config.model}
        if config.is_local_process():
            section['device'] = config.base_url.removeprefix('local://')
        else:
            section.update(base_url=config.base_url, api_key=str(credentials.get('api_key', '')))
        if config.thinking is not None:
            section['thinking'] = config.thinking
        cursor = models
        for key in keys[:-1]:
            cursor = cursor.setdefault(key, {})
        cursor[keys[-1]] = section
    check = copy.deepcopy(settings)
    apply_overrides(check, models)
    for slot in SLOT_PATHS:
        old, new = getattr(settings, slot), getattr(check, slot)
        if (old.model, old.api_key, old.base_url, old.thinking) != (new.model, new.api_key, new.base_url, new.thinking):
            raise ValueError('Migration would alter effective models; no files moved')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = _safe(directory / 'backups' / ('layout-' + stamp), project)
    plan = []
    for name in ('feishu-bot.json', 'feishu-bot.credentials.dpapi', 'models.override.toml', 'backups'):
        start = _safe(legacy / name, project)
        destination = _safe(directory / name if name.startswith('feishu-') else backup / ('legacy-models.override.toml' if name == 'models.override.toml' else 'legacy-config-backups'), project)
        if start.exists():
            if destination.exists():
                raise ValueError('Destination configuration exists; no file overwritten')
            plan.append((start, destination))
    kept = _remove_model_tables(text)
    suffix = ('\n[configuration]\nprivate_directory = "' + directory.relative_to(project).as_posix() + '"\n'
              '\n[models]\nfile = "' + target.relative_to(project).as_posix() + '"\n')
    replacement = kept + suffix
    # Do not silently rewrite an unusual TOML syntax/multiline literal as tables.
    cleaned = tomllib.loads(kept)
    original_data = tomllib.loads(text)
    for table in ('models', 'configuration'):
        original_data.pop(table, None)
    original_data.get('llm', {}).pop('provider', None)
    for parent, names in [('llm', ('main', 'fast', 'vl')), ('memory', ('embedding',))]:
        for name in names:
            original_data.get(parent, {}).pop(name, None)
        if parent in original_data and not original_data[parent]:
            original_data.pop(parent)
        if parent in cleaned and not cleaned[parent]:
            cleaned.pop(parent)
    if cleaned != original_data:
        raise ValueError('Unsupported model table syntax; non-model settings would change')
    tomllib.loads(replacement)
    backup.mkdir(parents=True, exist_ok=False, mode=0o700)
    shutil.copy2(source, backup / 'root-config.toml')
    moved = []
    try:
        write_model_file(target, models)
        for start, destination in plan:
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.move(str(start), str(destination))
            moved.append((start, destination))
        _replace_text(source, replacement)
        Settings.load(source)  # Full post-write validation, including env expressions.
    except BaseException:
        for start, destination in reversed(moved):
            start.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.move(str(destination), str(start))
        target.unlink(missing_ok=True)
        _replace_text(source, original)
        raise
    if legacy.is_dir() and not any(legacy.iterdir()):
        legacy.rmdir()  # Empty directory only; no recursive deletion.
    return {'already_migrated': False, 'moved_entries': len(moved), 'account_files_changed': 0,
            'models_file': str(target), 'private_configuration': str(directory), 'backup': str(backup)}
