"""Binary upload behind the existing account/body-size gateway. No multipart dependency."""

from typing import Literal
from urllib.parse import quote
from .kernel_dependency import require_kernel

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from ..memory.document_parser import DocumentParseError, MAX_FILE_BYTES
from ..memory.library import LibraryError

BINARY_BODY = {
    "requestBody": {
        "required": True,
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}


def document_import_router(kernel):
    router = APIRouter(tags=["non-conversation memory"], dependencies=[require_kernel(kernel)])

    @router.post("/api/library/import", status_code=201, openapi_extra=BINARY_BODY)
    @router.post("/api/memory-kernel/import", status_code=201, openapi_extra=BINARY_BODY)
    async def upload(
        request: Request,
        filename: str = Query(min_length=1, max_length=200),
        title: str = Query(default="", max_length=160),
        kind: Literal["note", "document", "task_result"] = "document",
        authorship: Literal["self", "external", "unknown"] = "unknown",
        scope: Literal["personal", "working"] = "personal",
        session_id: str | None = Query(default=None, min_length=1, max_length=64),
        encoding: Literal["auto", "utf-8", "utf-16", "gb18030"] = "auto",
        source_time: str | None = Query(default=None, max_length=64),
    ):
        if set(request.query_params) - {
            "filename",
            "title",
            "kind",
            "authorship",
            "scope",
            "session_id",
            "encoding",
            "source_time",
        }:
            raise LibraryError(422, "不接受额外导入参数，账号由正式登录决定")
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise LibraryError(413, "单文件上限1 MiB")
            chunks.append(chunk)
        try:
            from ..memory.observation_semantics import aware_time

            try:
                source_time = aware_time(source_time)
            except ValueError as exc:
                raise LibraryError(
                    422, "原始资料时间必须包含时区，不得填上传时间作为原始时间"
                ) from exc
            return await run_in_threadpool(
                kernel.ingest_file,
                filename,
                b"".join(chunks),
                title=title,
                kind=kind,
                authorship=authorship,
                scope=scope,
                session_id=session_id,
                encoding=encoding,
                source_time=source_time,
            )
        except DocumentParseError as exc:
            raise LibraryError(422, str(exc)) from exc

    @router.get("/api/library/sources/{sid}/file")
    def original(
        sid: str, revision_id: str | None = Query(default=None, min_length=1, max_length=64)
    ):
        filename, data = kernel.original_source_file(sid, revision_id)
        return Response(
            data,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router
