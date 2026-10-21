"""Read-only formation projections with source validation in the same snapshot."""

import json
from .studio import ProjectionError


class FormationRepository:
    def __init__(self, store):
        self.store = store

    def formation_observations(self, limit):
        from ..memory.formation_store import live_observation

        result = []
        with self.store.lock:
            rows = self.store.db.execute(
                "SELECT * FROM memory_observations WHERE state='active' ORDER BY observed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            for row in rows:
                try:
                    live_observation(self.store, row)
                except ValueError:
                    continue
                from ..memory.formation_history import visible
                from ..memory.observation_semantics import load

                result.append(
                    {
                        **dict(row),
                        "history": visible(self.store, row["id"]),
                        "semantics": load(self.store, "conversation", row["id"]),
                    }
                )
        return result

    def formation_patterns(self, limit):
        from ..memory.formation_store import pattern_sources

        result = []
        with self.store.lock:
            for row in self.store.db.execute(
                "SELECT * FROM memory_pattern_versions ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall():
                try:
                    sources = pattern_sources(self.store, row)
                except ValueError:
                    continue
                result.append({**dict(row), "evidence_message_ids": [s["id"] for s in sources]})
        return result

    def list_datasets(self):
        with self.store.lock:
            rows = self.store.db.execute(
                "SELECT dataset_id,manifest_json,imported_at FROM dataset_imports ORDER BY imported_at DESC"
            ).fetchall()
        return [
            {
                "dataset_id": r["dataset_id"],
                "manifest": json.loads(r["manifest_json"]),
                "imported_at": r["imported_at"],
            }
            for r in rows
        ]

    def dataset_receipt(self, dataset_id):
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT receipt_json FROM dataset_imports WHERE dataset_id=?", (dataset_id,)
            ).fetchone()
        if row is None:
            raise ProjectionError(404, "语料不存在")
        return json.loads(row["receipt_json"])
