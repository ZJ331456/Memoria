"""Project workspaces: registry, source binding and the task feedback loop.

Boundary contract (see docs/implemented/项目记忆DevMemory与任务闭环.md):
- memoria/memory is never modified; ingest/feedback/search/distill go through the
  MemoryKernel facade only.
- Project membership and task state live in project_* tables below; SQL in this
  file only touches those tables, inside Store.memory_transaction().
- Sources are scope='personal' library entries; a project is never a chat session
  and never becomes a personal Claim.
- No hidden model calls: import/recall/task recording are local; distill stays an
  explicit user action.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from ..memory.library import FeedbackInput, LibraryError, SourceInput
from .errors import ProjectError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_workspaces(
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, repo_path TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS project_sources(
    project_id TEXT NOT NULL REFERENCES project_workspaces(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
    origin TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(project_id, source_id));
CREATE TABLE IF NOT EXISTS project_tasks(
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project_workspaces(id) ON DELETE CASCADE,
    title TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
    source_id TEXT, created_at TEXT NOT NULL, finished_at TEXT);
CREATE INDEX IF NOT EXISTS idx_project_tasks ON project_tasks(project_id, status);
CREATE TABLE IF NOT EXISTS project_events(
    id INTEGER PRIMARY KEY, project_id TEXT NOT NULL, action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
"""

_TASK_KINDS = ("note", "document")
_OUTCOMES = ("success", "failure", "unknown")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class ProjectWorkspaces:
    """Business layer over the untouched memory core; no LLM calls of its own."""

    def __init__(self, store, kernel):
        self.store, self.kernel = store, kernel
        with store.memory_transaction():
            statement = ""
            for line in _SCHEMA.splitlines(keepends=True):
                statement += line
                if sqlite3.complete_statement(statement):
                    store.db.execute(statement)
                    statement = ""

    # ---- internal helpers -------------------------------------------------

    def _require(self, pid: str, *, allow_archived: bool = False) -> dict:
        row = self.store.db.execute(
            "SELECT * FROM project_workspaces WHERE id=?", (pid,)
        ).fetchone()
        if row is None:
            raise ProjectError(404, "项目不存在")
        project = dict(row)
        if project["status"] != "active" and not allow_archived:
            raise ProjectError(409, "项目已归档")
        return project

    def _event(self, pid: str, action: str, detail: str = "") -> None:
        self.store.db.execute(
            "INSERT INTO project_events(project_id,action,detail,created_at) VALUES(?,?,?,?)",
            (pid, action, detail[:500], _now()),
        )

    def _bind(self, pid: str, source: dict, *, task_id: str | None = None) -> None:
        """Bind an ingested source and, for task results, close the task — one transaction."""
        revision = source.get("revision") or {}
        with self.store.memory_transaction():
            self._require(pid)
            self.store.db.execute(
                "INSERT OR REPLACE INTO project_sources(project_id,source_id,kind,title,origin,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (pid, source["id"], source.get("kind", "document"),
                 (revision.get("title") or "")[:160], source.get("origin", ""), _now()),
            )
            if task_id:
                updated = self.store.db.execute(
                    "UPDATE project_tasks SET status='done', source_id=?, finished_at=?"
                    " WHERE id=? AND project_id=? AND status='open'",
                    (source["id"], _now(), task_id, pid),
                ).rowcount
                if updated != 1:
                    raise ProjectError(409, "任务已结束，来源已入库但未重复关闭任务")
                self._event(pid, "task_finished", f"{task_id} → {source['id']}")
            else:
                self._event(pid, "source_bound", source["id"])

    # ---- registry ---------------------------------------------------------

    def create(self, name: str, *, repo_path: str = "", description: str = "") -> dict:
        name = (name or "").strip()
        if not 1 <= len(name) <= 80:
            raise ProjectError(422, "项目名需 1-80 字符")
        pid = uuid.uuid4().hex
        with self.store.memory_transaction():
            try:
                self.store.db.execute(
                    "INSERT INTO project_workspaces(id,name,repo_path,description,status,created_at)"
                    " VALUES(?,?,?,?, 'active', ?)",
                    (pid, name, (repo_path or "").strip()[:500], (description or "").strip()[:2000], _now()),
                )
            except sqlite3.IntegrityError as exc:
                raise ProjectError(409, "同名项目已存在") from exc
            self._event(pid, "project_created", name)
        return self.detail(pid)

    def list(self) -> list[dict]:
        with self.store.lock:
            rows = self.store.db.execute(
                """SELECT p.*,
                    (SELECT count(*) FROM project_sources s WHERE s.project_id=p.id) AS sources,
                    (SELECT count(*) FROM project_tasks t WHERE t.project_id=p.id AND t.status='open') AS open_tasks,
                    (SELECT count(*) FROM project_tasks t WHERE t.project_id=p.id) AS tasks
                    FROM project_workspaces p WHERE p.status='active' ORDER BY p.created_at DESC"""
            ).fetchall()
            return [dict(row) for row in rows]

    def resolve(self, name_or_id: str) -> str:
        """Resolve a project name (exact) or id to an active project id."""
        key = (name_or_id or "").strip()
        if not key:
            raise ProjectError(422, "需要项目名称或 ID")
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT id,status FROM project_workspaces WHERE id=? OR name=?", (key, key)
            ).fetchone()
        if row is None:
            raise ProjectError(404, f"项目不存在：{key[:80]}")
        if row["status"] != "active":
            raise ProjectError(409, f"项目已归档：{key[:80]}")
        return row["id"]

    def archive(self, pid: str) -> dict:
        with self.store.memory_transaction():
            self._require(pid)
            self.store.db.execute(
                "UPDATE project_workspaces SET status='archived' WHERE id=?", (pid,)
            )
            self._event(pid, "project_archived")
        return {"id": pid, "status": "archived"}

    def detail(self, pid: str) -> dict:
        with self.store.lock:
            project = self._require(pid, allow_archived=True)
            sources = [dict(row) for row in self.store.db.execute(
                "SELECT * FROM project_sources WHERE project_id=? ORDER BY created_at DESC LIMIT 200",
                (pid,))]
            tasks = [dict(row) for row in self.store.db.execute(
                "SELECT * FROM project_tasks WHERE project_id=? ORDER BY created_at DESC LIMIT 200",
                (pid,))]
            events = [dict(row) for row in self.store.db.execute(
                "SELECT * FROM project_events WHERE project_id=? ORDER BY id DESC LIMIT 30", (pid,))]
        # Liveness via the kernel facade, outside the store lock; a retracted or
        # revised source keeps its binding snapshot but is reported honestly.
        for item in sources:
            try:
                head = self.kernel.source_detail(item["source_id"])
                item["live_status"] = head.get("status", "unknown")
                item["current_title"] = head.get("revision", {}).get("title", "")
                item["outcome"] = (head.get("feedback") or {}).get("outcome")
            except LibraryError:
                item["live_status"] = "unavailable"
        return {**project, "sources": sources, "tasks": tasks, "events": events}

    # ---- source import ----------------------------------------------------

    def import_git(self, pid: str, *, since: str | None = None,
                   max_commits: int = 200, include_body: bool = True) -> dict:
        from .git_source import collect_git_history

        with self.store.lock:
            project = self._require(pid)
        history = collect_git_history(
            project["repo_path"], since=since, max_commits=max_commits, include_body=include_body
        )
        origin = f"git-log:{history['repo_name']}@{history['head'][:12]}"
        source = self.kernel.ingest(SourceInput(
            kind="document", title=history["title"], content=history["text"],
            authorship="external", scope="personal", origin=origin,
        ))
        self._bind(pid, source)
        return {"source": source, "commits": history["count"],
                "head": history["head"], "truncated": len(history["text"]) >= 118_000}

    def import_text(self, pid: str, *, title: str, content: str,
                    kind: str = "document", authorship: str = "unknown") -> dict:
        if kind not in _TASK_KINDS:
            raise ProjectError(422, "项目资料类型只支持 note/document；任务结果走任务记录")
        if authorship not in ("self", "external", "unknown"):
            raise ProjectError(422, "作者归属不合法")
        with self.store.lock:
            self._require(pid)
        source = self.kernel.ingest(SourceInput(
            kind=kind, title=title, content=content, authorship=authorship,
            scope="personal", origin=f"project:{pid}",
        ))
        self._bind(pid, source)
        return source

    def import_file(self, pid: str, filename: str, data: bytes, *, title: str = "",
                    kind: str = "document", authorship: str = "unknown") -> dict:
        if kind not in _TASK_KINDS:
            raise ProjectError(422, "项目资料类型只支持 note/document；任务结果走任务记录")
        with self.store.lock:
            self._require(pid)
        source = self.kernel.ingest_file(
            filename, data, title=title, kind=kind, authorship=authorship, scope="personal"
        )
        self._bind(pid, source)
        return source

    # ---- task loop --------------------------------------------------------

    def create_task(self, pid: str, *, title: str, detail: str = "") -> dict:
        title = (title or "").strip()
        if not 1 <= len(title) <= 120:
            raise ProjectError(422, "任务标题需 1-120 字符")
        tid = uuid.uuid4().hex
        with self.store.memory_transaction():
            self._require(pid)
            self.store.db.execute(
                "INSERT INTO project_tasks(id,project_id,title,detail,status,created_at)"
                " VALUES(?,?,?,?,'open',?)",
                (tid, pid, title, (detail or "").strip()[:4000], _now()),
            )
            self._event(pid, "task_opened", title)
        return self._task(pid, tid)

    def finish_task(self, pid: str, tid: str, *, outcome: str, content: str,
                    authorship: str = "unknown") -> dict:
        """Record the result as a task_result source plus explicit feedback."""
        if outcome not in _OUTCOMES:
            raise ProjectError(422, "结果只能是 success/failure/unknown")
        if authorship not in ("self", "external", "unknown"):
            raise ProjectError(422, "作者归属不合法")
        with self.store.lock:
            project = self._require(pid)
            task = self._task(pid, tid)
            if task["status"] != "open":
                raise ProjectError(409, "任务已结束，请开新任务补充后续结果")
        # Kernel calls stay outside transactions; a failed feedback leaves the
        # source bound but the task open, which the next finish overwrites safely.
        source = self.kernel.ingest(SourceInput(
            kind="task_result", title=f"[{project['name']}] {task['title']}"[:160],
            content=content, authorship=authorship, scope="personal",
            origin=f"project-task:{pid}:{tid}", source_time=_now(),
        ))
        self.kernel.source_feedback(source["id"], FeedbackInput(
            expected_revision_id=source["revision"]["id"], outcome=outcome, content=content,
        ))
        self._bind(pid, source, task_id=tid)
        with self.store.lock:
            # Re-read through the facade so the returned source carries the feedback.
            return {"task": self._task(pid, tid),
                    "source": self.kernel.source_detail(source["id"]), "outcome": outcome}

    async def distill_task(self, pid: str, tid: str) -> dict:
        """Explicit LLM distillation of one task result; never called automatically."""
        with self.store.lock:
            self._require(pid)
            task = self._task(pid, tid)
        if not task["source_id"]:
            raise ProjectError(409, "任务尚未记录结果")
        return await self.kernel.distill_source(task["source_id"])

    def _task(self, pid: str, tid: str) -> dict:
        row = self.store.db.execute(
            "SELECT * FROM project_tasks WHERE id=? AND project_id=?", (tid, pid)
        ).fetchone()
        if row is None:
            raise ProjectError(404, "任务不存在")
        return dict(row)

    # ---- recall -----------------------------------------------------------

    def recall(self, pid: str, query: str, *, limit: int = 8, budget: int = 4000) -> dict:
        """Lexical search over the account library, filtered to this project's sources."""
        with self.store.lock:
            self._require(pid)
            members = {row["source_id"] for row in self.store.db.execute(
                "SELECT source_id FROM project_sources WHERE project_id=?", (pid,))}
            open_tasks = [dict(row) for row in self.store.db.execute(
                "SELECT id,title,detail,created_at FROM project_tasks"
                " WHERE project_id=? AND status='open' ORDER BY created_at DESC LIMIT 10", (pid,))]
        hits = self.kernel.search_sources(query, session_id=None, limit=limit, budget=budget)
        matched = [hit for hit in hits if hit.get("source_id") in members]
        return {
            "query": query, "hits": matched, "searched": len(hits),
            "project_sources": len(members), "open_tasks": open_tasks,
        }
