"""Temporary engineering inputs validate atomic ownership/provenance, not research quality."""
import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from memoria.store import Store
from scripts.prepare_real_dialogues import import_corpus


def test_atomic_import_idempotency_and_missing_sources(tmp_path):
    rows = [{'id': 'engineering-input', 'source_row': 0, 'dialog': ['temporary one', 'temporary two']}]
    subset = tmp_path / 'input.jsonl'
    subset.write_text(json.dumps(rows), encoding='utf-8')
    manifest = {'subset_sha256': hashlib.sha256(subset.read_bytes()).hexdigest(), 'kind': 'engineering-only'}
    directory = tmp_path / ('a' * 32)
    db = import_corpus(subset, rows, manifest, directory)
    assert import_corpus(subset, rows, manifest, directory) == db
    with closing(Store(db, 'json')) as store:
        assert len(store.sessions()) == 1
        assert store.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0] == 2
        assert store.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0] == 0
        assert store.db.execute('PRAGMA foreign_key_check').fetchall() == []
        with store.db:
            store.db.execute('UPDATE messages SET content=?', ('changed',))
    with pytest.raises(ValueError, match='removed or edited'):
        import_corpus(subset, rows, manifest, directory)

    # A duplicate source ID causes a constraint error; the entire batch rolls back.
    other = tmp_path / ('b' * 32)
    with pytest.raises(sqlite3.IntegrityError):
        import_corpus(subset, rows + rows, manifest, other)
    with closing(Store(other / 'memory/memoria.db', 'json')) as store:
        assert store.sessions() == []
        assert store.db.execute('SELECT COUNT(*) FROM messages_fts').fetchone()[0] == 0
        assert store.db.execute('SELECT COUNT(*) FROM dataset_imports').fetchone()[0] == 0
