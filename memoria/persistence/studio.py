"""Tenant-bound SQLite console projections; no HTTP or model dependencies."""

import json
import re
from typing import Literal


class ProjectionError(ValueError):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail
        super().__init__(detail)


def literal_pattern(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def readable_text(value: str) -> str:
    return re.sub(
        "(?<=[\\u3400-\\u9fff])[ \\t]+(?=[\\u3400-\\u9fff，。！？、；：])|(?<=[，。！？、；：])[ \\t]+(?=[\\u3400-\\u9fff])",
        "",
        value,
    )


def excerpt(text: str, query: str = "", limit: int = 160) -> str:
    text, query = (readable_text(text), readable_text(query))
    at = text.casefold().find(query.casefold()) if query else 0
    start = max(0, at - 35)
    return (
        ("…" if start else "")
        + text[start : start + limit]
        + ("…" if len(text) > start + limit else "")
    )


class StudioRepository:

    def __init__(self, store, owner_id):
        self.store, self.owner_id = (store, owner_id)
        with store.lock:
            store.db.create_function("studio_readable", 1, readable_text, deterministic=True)

    def origins(self):
        result = {}
        for row in self.store.db.execute("SELECT dataset_id,receipt_json FROM dataset_imports"):
            receipt = json.loads(row["receipt_json"])
            for item in receipt.get("source_mapping", []):
                if isinstance(item, dict) and isinstance(item.get("session_id"), str):
                    result[item["session_id"]] = {
                        "kind": "imported",
                        "dataset_id": row["dataset_id"],
                        "source_id": item.get("source_id"),
                    }
        return result

    def origin_for(self, session_id, sources):
        if session_id in sources:
            return sources[session_id]
        channels = [
            r[0]
            for r in self.store.db.execute(
                "SELECT DISTINCT COALESCE(o.channel,'unknown')\n            FROM messages m LEFT JOIN message_origins o ON o.message_id=m.id\n            WHERE m.session_id=? AND m.role='user' ORDER BY 1",
                (session_id,),
            )
        ]
        return {"kind": "native", "channels": channels or ["unknown"]}

    def browse(self, q, origin, sort, page, page_size):
        sources = self.origins()
        clauses = []
        params = []
        if q:
            clauses.append(
                "(studio_readable(s.title) LIKE ? ESCAPE '\\' OR EXISTS(SELECT 1 FROM messages m WHERE m.session_id=s.id AND studio_readable(m.content) LIKE ? ESCAPE '\\'))"
            )
            params.extend([literal_pattern(readable_text(q))] * 2)
        if origin in {"imported", "native"}:
            clauses.append(
                "s.id "
                + ("IN" if origin == "imported" else "NOT IN")
                + " (SELECT value FROM json_each(?))"
            )
            params.append(json.dumps(list(sources)))
        elif origin != "all":
            clauses.append("s.id NOT IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(list(sources)))
            clauses.append(
                "EXISTS(SELECT 1 FROM messages m LEFT JOIN message_origins o ON o.message_id=m.id\n                WHERE m.session_id=s.id AND m.role='user' AND COALESCE(o.channel,'unknown')=?)"
            )
            params.append(origin)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        total = self.store.db.execute("SELECT COUNT(*) FROM sessions s" + where, params).fetchone()[
            0
        ]
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        order = {
            "recent": "s.updated_at DESC,s.rowid ASC",
            "oldest": "s.created_at ASC,s.rowid ASC",
            "messages": "message_count DESC,s.rowid ASC",
        }[sort]
        rows = self.store.db.execute(
            "SELECT s.id,s.title,s.created_at,s.updated_at,\n            (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) AS message_count,\n            (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id AND studio_readable(m.content) LIKE ? ESCAPE '\\') AS matching_messages,\n            COALESCE((SELECT m.id FROM messages m WHERE m.session_id=s.id AND studio_readable(m.content) LIKE ? ESCAPE '\\' ORDER BY m.rowid LIMIT 1),\n                (SELECT m.id FROM messages m WHERE m.session_id=s.id ORDER BY m.rowid LIMIT 1)) AS preview_message_id\n            FROM sessions s"
            + where
            + " ORDER BY "
            + order
            + " LIMIT ? OFFSET ?",
            [
                literal_pattern(readable_text(q)),
                literal_pattern(readable_text(q)),
                *params,
                page_size,
                (page - 1) * page_size,
            ],
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            text = self.store.db.execute(
                "SELECT content FROM messages WHERE id=?", (item["preview_message_id"],)
            ).fetchone()
            item.update(
                preview=excerpt(text[0], q) if text else "",
                origin=self.origin_for(item["id"], sources),
            )
            if not q:
                item["matching_messages"] = 0
            items.append(item)
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
            "query": q,
        }

    def summary(self):
        with self.store.lock:
            counts = self.store.overview()
            current = self.store.db.execute(
                "SELECT r.status,COUNT(*) n FROM claim_roots c JOIN claim_revisions r ON r.claim_id=c.id\n                WHERE c.user_id=? AND r.revision=(SELECT MAX(r2.revision) FROM claim_revisions r2 WHERE r2.claim_id=c.id)\n                GROUP BY r.status",
                (self.owner_id,),
            ).fetchall()
            grouped = {row["status"]: row["n"] for row in current}
            reviews = self.store.db.execute(
                "SELECT COUNT(*) FROM memory_reviews WHERE status='pending'"
            ).fetchone()[0]
            return {
                **counts,
                "claims": sum(grouped.values()),
                "claim_statuses": grouped,
                "pending_reviews": reviews,
                "datasets": self.store.db.execute(
                    "SELECT COUNT(*) FROM dataset_imports"
                ).fetchone()[0],
                "imported_conversations": self.store.db.execute(
                    "SELECT COUNT(*) FROM sessions WHERE id IN (SELECT value FROM json_each(?))",
                    (json.dumps(list(self.origins())),),
                ).fetchone()[0],
                "recent": self.browse("", "all", "recent", 1, 5)["items"],
                "scope": "current_authenticated_user",
                "model_called": False,
            }

    def conversations(
        self,
        q: str = "",
        origin: Literal[
            "all",
            "imported",
            "native",
            "chat",
            "api",
            "feishu",
            "qq",
            "telegram",
            "internal",
            "unknown",
        ] = "all",
        sort: Literal["recent", "oldest", "messages"] = "recent",
        page: int = 1,
        page_size: int = 20,
    ):
        with self.store.lock:
            return self.browse(q.strip(), origin, sort, page, page_size)

    def memory_status(self, session_id: str, ids: str = ""):
        from ..memory.processing import message_states

        with self.store.lock:
            if not self.store.session(session_id):
                raise ProjectionError(404, "会话不存在")
            selected = (
                list(dict.fromkeys(ids.split(",")))
                if ids
                else [m["id"] for m in self.store.messages(session_id, 100)]
            )
            if len(selected) > 200:
                raise ProjectionError(422, "一次最多读取200条消息状态")
            for mid in selected:
                if not self.store.db.execute(
                    "SELECT 1 FROM messages WHERE id=? AND session_id=?", (mid, session_id)
                ).fetchone():
                    raise ProjectionError(404, "消息不属于当前会话或已删除")
            return {"items": message_states(self.store, selected), "model_called": False}

    def messages(
        self, session_id: str, page: int = 1, page_size: int = 50, q: str = "", anchor_id: str = ""
    ):
        with self.store.lock:
            session = self.store.session(session_id)
            if not session:
                raise ProjectionError(404, "会话不存在")
            total = self.store.db.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            pages = max(1, (total + page_size - 1) // page_size)
            if anchor_id:
                anchor = self.store.db.execute(
                    "SELECT rowid FROM messages WHERE id=? AND session_id=?",
                    (anchor_id, session_id),
                ).fetchone()
                if not anchor:
                    raise ProjectionError(404, "消息不属于当前会话")
                ordinal = self.store.db.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id=? AND rowid<=?",
                    (session_id, anchor[0]),
                ).fetchone()[0]
                page = (ordinal - 1) // page_size + 1
            page = min(page, pages)
            offset = (page - 1) * page_size
            rows = self.store.db.execute(
                "SELECT id,session_id,role,content,created_at FROM messages WHERE session_id=? ORDER BY rowid LIMIT ? OFFSET ?",
                (session_id, page_size, offset),
            ).fetchall()
            q = q.strip()
            matches = []
            matched_total = 0
            if q:
                pattern = literal_pattern(readable_text(q))
                matched_total = self.store.db.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id=? AND studio_readable(content) LIKE ? ESCAPE '\\'",
                    (session_id, pattern),
                ).fetchone()[0]
                matched = self.store.db.execute(
                    "WITH ranked AS(SELECT id,content,ROW_NUMBER() OVER(ORDER BY rowid) ordinal FROM messages WHERE session_id=?)\n                    SELECT id,ordinal FROM ranked WHERE studio_readable(content) LIKE ? ESCAPE '\\' ORDER BY ordinal LIMIT 200",
                    (session_id, pattern),
                ).fetchall()
                matches = [
                    {
                        "id": row["id"],
                        "ordinal": row["ordinal"],
                        "page": (row["ordinal"] - 1) // page_size + 1,
                    }
                    for row in matched
                ]
            from ..memory.processing import message_states

            states = message_states(self.store, [row["id"] for row in rows])
            return {
                "session": {
                    **session,
                    "message_count": total,
                    "origin": self.origin_for(session_id, self.origins()),
                },
                "items": [
                    {
                        **dict(row),
                        **states[row["id"]],
                        "display_content": readable_text(row["content"]),
                        "ordinal": offset + i + 1,
                    }
                    for i, row in enumerate(rows)
                ],
                "total": total,
                "page": page,
                "page_size": page_size,
                "pages": pages,
                "matches": matches,
                "matched_total": matched_total,
                "matches_truncated": matched_total > len(matches),
                "time_basis": "recorded_at; imported messages do not establish event dates",
            }

    def memories(
        self,
        q: str = "",
        status: Literal["active", "superseded", "all"] = "active",
        page: int = 1,
        page_size: int = 50,
    ):
        clauses = []
        params = []
        if q.strip():
            clauses.append("content LIKE ? ESCAPE '\\'")
            params.append(literal_pattern(q.strip()))
        if status != "all":
            clauses.append("status=?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.store.lock:
            total = self.store.db.execute(
                "SELECT COUNT(*) FROM memories" + where, params
            ).fetchone()[0]
            page = min(page, max(1, (total + page_size - 1) // page_size))
            rows = self.store.db.execute(
                "SELECT id,content,kind,importance,source,source_ref,status,created_at,updated_at,reinforcement FROM memories"
                + where
                + " ORDER BY updated_at DESC,rowid DESC LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
            return {
                "items": [dict(row) for row in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
                "model_called": False,
            }

    def pipeline_snapshot(self):
        """Explain persisted stages without invoking an extractor or opening secrets."""
        from ..memory.experience import personal_source_sql

        with self.store.lock:
            counts = self.store.overview()
            imported = {
                r["id"]
                for r in self.store.db.execute(
                    "SELECT id FROM sessions WHERE id IN (SELECT value FROM json_each(?))",
                    (json.dumps(list(self.origins())),),
                )
            }
            eligible = self.store.db.execute(
                "SELECT COUNT(*) FROM messages m WHERE " + personal_source_sql("m")
            ).fetchone()[0]
            job_counts = {
                r["status"]: r["n"]
                for r in self.store.db.execute(
                    "SELECT status,COUNT(*) n FROM memory_jobs GROUP BY status"
                )
            }
            review_counts = {
                r["status"]: r["n"]
                for r in self.store.db.execute(
                    "SELECT status,COUNT(*) n FROM memory_reviews GROUP BY status"
                )
            }
            recent = [
                dict(r)
                for r in self.store.db.execute(
                    "SELECT j.id,j.source_ref,j.status,j.attempts,j.created_at,j.updated_at,\n                j.extracted_count,j.staged_count,m.session_id,\n                (SELECT COUNT(*) FROM memory_reviews r WHERE r.job_id=j.id) review_count,\n                CASE WHEN j.error IS NULL THEN 0 ELSE 1 END has_error\n                FROM memory_jobs j LEFT JOIN messages m ON m.id=j.source_ref ORDER BY j.created_at DESC LIMIT 50"
                )
            ]
            from ..memory.processing import message_states

            recent_states = message_states(self.store, [r["source_ref"] for r in recent])
            for job in recent:
                job.update(recent_states.get(job["source_ref"], {}))
            legacy = self.store.db.execute(
                "SELECT COUNT(*) FROM memory_jobs WHERE status='completed' AND extracted_count IS NULL"
            ).fetchone()[0]
            known_empty = self.store.db.execute(
                "SELECT COUNT(*) FROM memory_jobs WHERE status='completed' AND extracted_count=0"
            ).fetchone()[0]
            claim_statuses = {
                r["status"]: r["n"]
                for r in self.store.db.execute(
                    "SELECT r.status,COUNT(*) n FROM claim_roots c JOIN claim_revisions r\n                ON r.claim_id=c.id WHERE c.user_id=? AND r.revision=(SELECT MAX(x.revision) FROM claim_revisions x WHERE x.claim_id=c.id) GROUP BY r.status",
                    (self.owner_id,),
                )
            }
            namespace = self.store.embedding_namespace
            indexes = {
                "memory_vectors": self.store.db.execute(
                    "SELECT COUNT(*) FROM memory_embeddings WHERE namespace=?", (namespace,)
                ).fetchone()[0],
                "memory_chunks": self.store.db.execute(
                    "SELECT COUNT(*) FROM memory_embedding_chunks WHERE namespace=?", (namespace,)
                ).fetchone()[0],
                "experience_chunks": self.store.db.execute(
                    "SELECT COUNT(*) FROM experience_chunks c JOIN messages m ON m.id=c.message_id WHERE "
                    + personal_source_sql("m")
                ).fetchone()[0],
                "experience_vectors": self.store.db.execute(
                    "SELECT COUNT(*) FROM experience_vectors WHERE namespace=?", (namespace,)
                ).fetchone()[0],
            }
        return (
            counts,
            imported,
            eligible,
            job_counts,
            review_counts,
            recent,
            legacy,
            known_empty,
            claim_statuses,
            indexes,
        )
