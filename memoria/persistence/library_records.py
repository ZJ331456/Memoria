"""Read-only native source records used by entity indexes and the library domain."""

from __future__ import annotations
import hashlib


class LibraryRecordRepository:
    def __init__(self, store):
        self.store = store

    def _source(self, sid, *, active=True):
        from ..memory.library import LibraryError

        row = self.store.db.execute("SELECT * FROM library_sources WHERE id=?", (sid,)).fetchone()
        if not row or (active and row["status"] != "active"):
            raise LibraryError(404, "资料不存在或已撤回")
        return dict(row)

    def _revision(self, rid):
        from ..memory.library import LibraryError

        row = self.store.db.execute("SELECT * FROM library_revisions WHERE id=?", (rid,)).fetchone()
        if not row or hashlib.sha256(row["content"].encode()).hexdigest() != row["sha"]:
            raise LibraryError(409, "资料版本或内容指纹失效")
        return dict(row)
