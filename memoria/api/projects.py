"""Project workspace routes. Business rules live in memoria/projects; this module
only adapts HTTP, and every memory operation stays behind the Kernel facade."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from ..memory.document_parser import DocumentParseError, MAX_FILE_BYTES
from ..memory.library import LibraryError
from .kernel_dependency import require_kernel


def require_projects(workspaces):
    def check():
        if workspaces is None:
            raise HTTPException(503, "A tenant-bound project workspace is required")

    return Depends(check)


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=80)
    repo_path: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=2000)


class GitImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    since: str | None = Field(default=None, max_length=64)
    max_commits: int = Field(default=200, ge=1, le=2000)
    include_body: bool = True


class TextImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=120000)
    kind: Literal["note", "document"] = "document"
    authorship: Literal["self", "external", "unknown"] = "unknown"


class TaskCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=120)
    detail: str = Field(default="", max_length=4000)


class TaskFinishBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    outcome: Literal["success", "failure", "unknown"]
    content: str = Field(min_length=1, max_length=3000)
    authorship: Literal["self", "external", "unknown"] = "unknown"


class RecallBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=8, ge=1, le=8)
    budget: int = Field(default=4000, ge=800, le=8000)


BINARY_BODY = {
    "requestBody": {
        "required": True,
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}


def projects_router(workspaces):
    router = APIRouter(
        prefix="/api/projects",
        tags=["project memory"],
        dependencies=[
            require_projects(workspaces),
            require_kernel(workspaces.kernel if workspaces else None),
        ],
    )

    @router.post("", status_code=201)
    def create(body: ProjectCreate):
        return workspaces.create(body.name, repo_path=body.repo_path, description=body.description)

    @router.get("")
    def list_projects():
        return workspaces.list()

    @router.get("/{pid}")
    def detail(pid: str):
        return workspaces.detail(pid)

    @router.delete("/{pid}")
    def archive(pid: str):
        return workspaces.archive(pid)

    @router.post("/{pid}/import/git")
    def import_git(pid: str, body: GitImportBody):
        return workspaces.import_git(
            pid, since=body.since, max_commits=body.max_commits, include_body=body.include_body
        )

    @router.post("/{pid}/import/text")
    def import_text(pid: str, body: TextImportBody):
        return workspaces.import_text(
            pid, title=body.title, content=body.content, kind=body.kind, authorship=body.authorship
        )

    @router.post("/{pid}/import/file", status_code=201, openapi_extra=BINARY_BODY)
    async def import_file(
        request: Request,
        pid: str,
        filename: str = Query(min_length=1, max_length=200),
        title: str = Query(default="", max_length=160),
        kind: Literal["note", "document"] = "document",
        authorship: Literal["self", "external", "unknown"] = "unknown",
        encoding: Literal["auto", "utf-8", "utf-16", "gb18030"] = "auto",
    ):
        if set(request.query_params) - {"filename", "title", "kind", "authorship", "encoding"}:
            raise LibraryError(422, "不接受额外导入参数，账号由正式登录决定")
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise LibraryError(413, "单文件上限1 MiB")
            chunks.append(chunk)
        try:
            return await run_in_threadpool(
                workspaces.import_file, pid, filename, b"".join(chunks),
                title=title, kind=kind, authorship=authorship,
            )
        except DocumentParseError as exc:
            raise LibraryError(422, str(exc)) from exc

    @router.post("/{pid}/tasks", status_code=201)
    def create_task(pid: str, body: TaskCreateBody):
        return workspaces.create_task(pid, title=body.title, detail=body.detail)

    @router.post("/{pid}/tasks/{tid}/finish")
    def finish_task(pid: str, tid: str, body: TaskFinishBody):
        return workspaces.finish_task(
            pid, tid, outcome=body.outcome, content=body.content, authorship=body.authorship
        )

    @router.post("/{pid}/tasks/{tid}/distill")
    async def distill_task(pid: str, tid: str):
        return await workspaces.distill_task(pid, tid)

    @router.post("/{pid}/recall")
    def recall(pid: str, body: RecallBody):
        return workspaces.recall(pid, body.query, limit=body.limit, budget=body.budget)

    return router
