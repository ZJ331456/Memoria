"""Shared TOML persistence for the four administrator-owned configuration files."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import tomllib
from pathlib import Path

_LOCK = threading.RLock()


def read_document(path: Path) -> dict:
    with path.open('rb') as handle:
        return tomllib.load(handle)


def _key(value: str) -> str:
    return value if re.fullmatch(r'[A-Za-z0-9_-]+', value) else json.dumps(value, ensure_ascii=False)


def _scalar(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return json.dumps(value, allow_nan=False)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return '[' + ', '.join(_scalar(item) for item in value) + ']'
    if isinstance(value, dict):
        return '{' + ', '.join(_key(k) + ' = ' + _scalar(v) for k, v in value.items()) + '}'
    raise ValueError('Unsupported configuration value type')


def dump_toml(data: dict, prefix: str = '') -> str:
    lines = []
    def table(values, parts):
        if parts:
            lines.append('[' + '.'.join(_key(p) for p in parts) + ']')
        for key, value in values.items():
            if not isinstance(value, dict):
                lines.append(_key(key) + ' = ' + _scalar(value))
        lines.append('')
        for key, value in values.items():
            if isinstance(value, dict):
                table(value, parts + [key])
    table(data, prefix.split('.') if prefix else [])
    return '\n'.join(lines).strip() + '\n'


def write_document(path: Path, data: dict) -> None:
    """Validate before publication; failed replacement leaves the old file intact."""
    value = dump_toml(data)
    if tomllib.loads(value) != data:
        raise ValueError('Configuration serialization changed the document')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         prefix='.config-', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def update_document(path: Path, change) -> dict:
    # Administrative commands run locally. Detect concurrent edits before replace;
    # this is not a distributed configuration/hot-reload service.
    with _LOCK:
        before = path.read_bytes()
        document = tomllib.loads(before.decode('utf-8-sig'))
        change(document)
        if path.read_bytes() != before:
            raise ValueError('Configuration changed concurrently; retry the operation')
        write_document(path, document)
        return document
