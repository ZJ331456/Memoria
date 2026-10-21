"""Review projections; authority decisions remain in PublicationPolicy."""

import json


class ReviewRepository:
    def __init__(self, store):
        self.store = store

    def formation_pattern(self, review_id):
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT pattern_id FROM memory_formation_candidates WHERE review_id=?", (review_id,)
            ).fetchone()
            return row["pattern_id"] if row else None

    def edit_pending(self, review_id, content, kind, importance):
        from ..store import now

        # Caller holds its source-validation transaction; this operation joins it.
        with self.store._memory_transaction():
            return bool(
                self.store.db.execute(
                    "UPDATE memory_reviews SET content=?,kind=?,importance=?,updated_at=? WHERE id=? AND status='pending'",
                    (content, kind, importance, now(), review_id),
                ).rowcount
            )

    def pending_targets(self, claim_ids):
        with self.store.lock:
            return [
                dict(row)
                for row in self.store.db.execute(
                    """
                SELECT r.*,a.data_json FROM memory_reviews r
                JOIN memory_review_assessments a ON a.id=(
                    SELECT MAX(id) FROM memory_review_assessments WHERE review_id=r.id)
                WHERE r.status IN('pending','approved')
                  AND json_extract(a.data_json,'$.structured_proposal.target_claim_id')
                      IN(SELECT value FROM json_each(?))
                ORDER BY r.rowid DESC LIMIT 64
            """,
                    (json.dumps(claim_ids),),
                )
            ]
