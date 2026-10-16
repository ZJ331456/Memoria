"""Optional local SQLite viewer setup; never imported by the application."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MEMORY_SQL = """-- Select a USER MEMORY connection. Ctrl+Enter runs the current statement.
-- Read-only inspection, no extraction, approvals or model calls.
SELECT 'sessions' AS item, count(*) AS total FROM sessions
UNION ALL SELECT 'messages', count(*) FROM messages
UNION ALL SELECT 'memories', count(*) FROM memories
UNION ALL SELECT 'memory_jobs', count(*) FROM memory_jobs
UNION ALL SELECT 'memory_reviews', count(*) FROM memory_reviews
UNION ALL SELECT 'claim_roots', count(*) FROM claim_roots;

SELECT status, count(*) AS total FROM memory_jobs GROUP BY status;
SELECT status, count(*) AS total FROM memory_reviews GROUP BY status;
SELECT channel, count(*) AS total FROM message_origins GROUP BY channel;

-- Missing origins are UNKNOWN, not necessarily Chat.
SELECT m.id, m.session_id, m.role, COALESCE(o.channel, 'unknown') AS channel,
       datetime(m.created_at, 'localtime') AS local_time, m.content
FROM messages m LEFT JOIN message_origins o ON o.message_id = m.id
ORDER BY m.created_at DESC, m.id LIMIT 100;

SELECT id, status, extracted_count, staged_count, attempts, error,
       datetime(updated_at, 'localtime') AS updated_local
FROM memory_jobs ORDER BY updated_at DESC LIMIT 100;

SELECT id, kind, status, content FROM memories ORDER BY updated_at DESC LIMIT 100;
"""
ACCOUNTS_SQL = """-- Select the ACCOUNTS connection. No password hashes or login tokens.
SELECT id, username, role, password_hash IS NOT NULL AS login_enabled,
       datetime(created_at, 'unixepoch', 'localtime') AS created_local
FROM users ORDER BY username;

SELECT action, count(*) AS total FROM account_events GROUP BY action;
"""
CHANNEL_SQL = """-- Select ONE CHANNEL connection; do not expose external sender IDs or messages.
SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name;
SELECT status, count(*) AS total FROM inbox GROUP BY status;
"""


def read_only(path: Path) -> sqlite3.Connection:
    # mode=ro also prevents silently creating an empty database at a wrong path.
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)


def build_connections(data_root: Path, usernames: list[str]) -> dict:
    data_root = data_root.resolve(strict=True)
    targets = [(data_root / 'auth/accounts.db', 'Accounts', '01 账号与权限')]
    with closing(read_only(targets[0][0])) as accounts:
        for username in dict.fromkeys(usernames):
            row = accounts.execute('SELECT id, username FROM users WHERE username=?',
                                   (username.strip().lower(),)).fetchone()
            if not row or not re.fullmatch(r'[0-9a-f]{32}', row[0]):
                raise ValueError('Requested account is missing or has an invalid server ID')
            target = data_root / 'users' / row[0] / 'memory/memoria.db'
            resolved = target.resolve(strict=True)
            if resolved != target or not resolved.is_relative_to(data_root / 'users'):
                raise ValueError('Refusing a linked or out-of-root user database')
            targets.append((target, f'Memory - {row[1]}', '02 用户记忆'))
    for channel in ('feishu', 'qq', 'telegram'):
        path = data_root / 'channels' / f'{channel}.db'
        if path.is_file():
            targets.append((path, f'Channel - {channel}', '03 通讯渠道'))
    connections, folders = {}, {}
    for path, label, group in targets:
        resolved = path.resolve(strict=True)
        if resolved != path or not resolved.is_relative_to(data_root):
            raise ValueError('Refusing a linked or out-of-root database')
        with closing(read_only(path)) as db:
            db.execute('SELECT count(*) FROM sqlite_schema').fetchone()
        folder = f'Memoria - {group}'
        folders[folder] = {'name': folder}
        cid = 'sqlite_jdbc-memoria-' + hashlib.sha256(str(path).encode()).hexdigest()[:20]
        connections[cid] = {
            'provider': 'sqlite', 'driver': 'sqlite_jdbc',
            'name': f'Memoria - {label} [READ ONLY]', 'folder': folder,
            'description': 'Optional local inspection. Change memory through Memoria APIs/Studio.',
            'save-password': False, 'read-only': True,
            'configuration': {
                'database': str(path),
                'url': 'jdbc:sqlite:' + path.as_uri() + '?mode=ro',
                'configurationType': 'URL', 'type': 'dev',
                'properties': {'open_mode': '1', 'busy_timeout': '3000'},
                'bootstrap': {'autocommit': True, 'query': ['PRAGMA query_only=ON'],
                              'ignoreErrors': False},
            },
        }
    return {'folders': folders, 'connections': connections}


def ensure_closed() -> None:
    if os.name != 'nt':
        raise ValueError('Automatic workspace setup is currently Windows-only')
    result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq dbeaver.exe', '/FO', 'CSV', '/NH'],
                            capture_output=True, text=True, check=True)
    if any(row and row[0].lower() == 'dbeaver.exe'
           for row in csv.reader(io.StringIO(result.stdout))):
        raise ValueError('Close DBeaver before applying connection definitions; backend may stay running')


def apply_workspace(workspace: Path, definitions: dict) -> Path:
    project = workspace.resolve(strict=True) / 'General'
    if not (project / '.project').is_file():
        raise ValueError('Open DBeaver once to initialize this workspace first')
    config_dir = project / '.dbeaver'
    config_dir.mkdir(exist_ok=True)
    target = config_dir / 'data-sources-memoria.json'
    scripts = project / 'Scripts'
    outputs = {target: json.dumps(definitions, ensure_ascii=False, indent=2) + '\n',
               scripts / 'Memoria-memory.sql': MEMORY_SQL,
               scripts / 'Memoria-accounts.sql': ACCOUNTS_SQL,
               scripts / 'Memoria-channels.sql': CHANNEL_SQL}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    for path, text in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            # The ordinary data-sources.json and unrelated SQL are never touched.
            if path.read_text(encoding='utf-8') == text:
                continue
            backup = config_dir / 'memoria-backups' / stamp / path.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
        handle, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix='.tmp')
        try:
            with os.fdopen(handle, 'w', encoding='utf-8', newline='\n') as out:
                out.write(text)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', action='append', required=True, help='Existing account; repeat to add another')
    parser.add_argument('--config', help='System configuration path')
    parser.add_argument('--workspace', type=Path, default=Path(os.environ.get('APPDATA', '.')) /
                        'DBeaverData/workspace6')
    parser.add_argument('--apply', action='store_true', help='Write DBeaver definitions after preview')
    args = parser.parse_args()
    from memoria.config import Settings
    try:
        definitions = build_connections(Settings.load(args.config).database.parent, args.user)
        for connection in definitions['connections'].values():
            print(connection['name'])
        if args.apply:
            ensure_closed()
            print('Saved:', apply_workspace(args.workspace, definitions))
            print('Open DBeaver; definitions and three SQL guides are ready.')
        else:
            print('Preview only. Add --apply with DBeaver closed to save definitions.')
    except (ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
        parser.exit(1, f'DBeaver setup failed: {exc}\n')


if __name__ == '__main__':
    main()
