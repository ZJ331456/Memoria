"""The optional viewer must not create production databases or grant writes."""
import sqlite3
from contextlib import closing

import pytest

from scripts.configure_dbeaver import apply_workspace, build_connections


@pytest.fixture
def viewer_data(tmp_path):
    root = tmp_path / 'data'
    (root / 'auth').mkdir(parents=True)
    uid = 'a' * 32
    with closing(sqlite3.connect(root / 'auth/accounts.db')) as db:
        db.execute('CREATE TABLE users (id TEXT, username TEXT)')
        db.execute('INSERT INTO users VALUES (?, ?)', (uid, 'alice'))
        db.commit()
    memory = root / 'users' / uid / 'memory/memoria.db'
    memory.parent.mkdir(parents=True)
    with closing(sqlite3.connect(memory)) as db:
        db.execute('CREATE TABLE memories (content TEXT)')
    return root, memory


def test_driver_url_rejects_writes_and_reads_current_wal(viewer_data):
    root, memory = viewer_data
    with closing(sqlite3.connect(memory)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute("INSERT INTO memories VALUES ('committed')")
        writer.commit()
        definitions = build_connections(root, ['alice'])
        config = next(c['configuration'] for c in definitions['connections'].values()
                      if c['configuration']['database'] == str(memory))
        with closing(sqlite3.connect(config['url'].removeprefix('jdbc:sqlite:'), uri=True)) as db:
            assert db.execute('SELECT content FROM memories').fetchone() == ('committed',)
            with pytest.raises(sqlite3.OperationalError, match='readonly'):
                db.execute("INSERT INTO memories VALUES ('forbidden')")


def test_unknown_user_does_not_create_database(viewer_data):
    root, _ = viewer_data
    before = set(root.rglob('*'))
    with pytest.raises(ValueError, match='account is missing'):
        build_connections(root, ['missing'])
    assert set(root.rglob('*')) == before


def test_missing_tenant_database_stays_missing(viewer_data):
    root, memory = viewer_data
    memory.unlink()
    with pytest.raises(FileNotFoundError):
        build_connections(root, ['alice'])
    assert not memory.exists()


def test_corrupt_account_id_cannot_escape_data_root(viewer_data):
    root, _ = viewer_data
    with closing(sqlite3.connect(root / 'auth/accounts.db')) as db:
        db.execute("UPDATE users SET id='../../outside'")
        db.commit()
    with pytest.raises(ValueError, match='invalid server ID'):
        build_connections(root, ['alice'])


def test_setup_keeps_unrelated_connections_and_backs_up_own_file(viewer_data, tmp_path):
    root, _ = viewer_data
    workspace = tmp_path / 'workspace'
    project = workspace / 'General'
    private = project / '.dbeaver'
    private.mkdir(parents=True)
    (project / '.project').write_text('<projectDescription/>')
    ordinary = private / 'data-sources.json'
    ordinary.write_bytes(b'{"connections":{"existing":{}}}')
    old = private / 'data-sources-memoria.json'
    old.write_bytes(b'{"connections":{}}')
    result = apply_workspace(workspace, build_connections(root, ['alice']))
    assert result == old
    assert ordinary.read_bytes() == b'{"connections":{"existing":{}}}'
    backups = list((private / 'memoria-backups').rglob('*.json'))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b'{"connections":{}}'
    apply_workspace(workspace, build_connections(root, ['alice']))
    assert len(list((private / 'memoria-backups').rglob('*.json'))) == 1
