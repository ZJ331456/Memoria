"""Tenant-bound conversations routes."""

from __future__ import annotations
from fastapi import APIRouter
from .context import TenantApiContext
import asyncio
import json
from contextlib import suppress
from typing import Any
from fastapi import HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from ..provenance import message_ingress
from .schemas import (
    CancelResponse,
    ChatBody,
    ChatResponse,
    MessageResponse,
    MessageSourceResponse,
    SessionBody,
    SessionPatch,
    SessionResponse,
)


def conversations_router(context: TenantApiContext):
    router = APIRouter()
    active_turns = context.active_turns
    app = context.app
    deleting_sessions = context.deleting_sessions
    service = context.service
    session_locks = context.session_locks
    store = context.store

    @router.get(
        "/api/sessions", response_model=list[SessionResponse], tags=["sessions"], summary="列出会话"
    )
    def sessions():
        return store.sessions()

    @router.post(
        "/api/sessions",
        response_model=SessionResponse,
        status_code=201,
        tags=["sessions"],
        summary="创建会话",
    )
    def create_session(body: SessionBody):
        return {**store.create_session(body.title), "message_count": 0}

    @router.get(
        "/api/sessions/{session_id}",
        response_model=SessionResponse,
        tags=["sessions"],
        summary="获取会话",
    )
    def session(session_id: str):
        item = store.session(session_id)
        if not item:
            raise HTTPException(404, "会话不存在")
        item["message_count"] = len(store.messages(session_id, 10000))
        return item

    @router.patch(
        "/api/sessions/{session_id}",
        response_model=SessionResponse,
        tags=["sessions"],
        summary="重命名会话",
    )
    def update_session(session_id: str, body: SessionPatch):
        if not store.session(session_id):
            raise HTTPException(404, "会话不存在")
        store.rename_session(session_id, body.title)
        item = store.session(session_id) or {}
        item["message_count"] = len(store.messages(session_id, 10000))
        return item

    @router.get(
        "/api/sessions/{session_id}/messages",
        response_model=list[MessageResponse],
        tags=["sessions"],
        summary="获取会话消息",
    )
    def messages(
        session_id: str, limit: int = Query(default=100, ge=1, le=1000), anchor_id: str = ""
    ):
        if not store.session(session_id):
            raise HTTPException(404, "会话不存在")
        if anchor_id:
            source = store.message_source(anchor_id)
            if not source or source["session_id"] != session_id:
                raise HTTPException(404, "来源消息不属于该会话或已删除")
        return store.messages(session_id, limit, anchor_id or None)

    @router.get(
        "/api/messages/{message_id}/source",
        response_model=MessageSourceResponse,
        tags=["sessions"],
        summary="按来源 ID 定位原始消息",
    )
    def message_source(message_id: str):
        item = store.message_source(message_id)
        if not item:
            raise HTTPException(404, "来源消息不存在，可能已删除")
        return item

    def release_turn(session_id, task, lock):
        if task is not None and active_turns.get(session_id) is task:
            active_turns.pop(session_id, None)
        if (
            session_id not in active_turns
            and not lock.locked()
            and session_locks.get(session_id) is lock
        ):
            session_locks.pop(session_id, None)

    async def execute_turn(session_id: str, content: str, request_id: str | None = None):
        if session_id in deleting_sessions:
            raise HTTPException(409, "该会话正在删除")
        if not store.session(session_id):
            raise HTTPException(404, "会话不存在")
        lock = session_locks.setdefault(session_id, asyncio.Lock())
        if lock.locked() or session_id in active_turns:
            raise HTTPException(409, "该会话已有一轮对话正在执行")
        task = None
        try:
            async with lock:
                if session_id in deleting_sessions:
                    raise HTTPException(409, "该会话正在删除")
                if not store.session(session_id):
                    raise HTTPException(404, "会话不存在")
                with message_ingress("api", preserve=True):
                    task = asyncio.create_task(
                        service.chat_with_trace(session_id, content.strip(), request_id=request_id)
                    )
                active_turns[session_id] = task
                task.add_done_callback(lambda done: release_turn(session_id, done, lock))
                message, memories, trace = await task
            return {"message": message, "memories_created": memories, "trace": trace}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(502, f"模型调用失败：{type(exc).__name__}: {exc}") from exc
        finally:
            release_turn(session_id, task, lock)

    # Trusted bot adapters share this owner-bound turn gate with HTTP Chat.
    app.state.chat_turn = execute_turn

    @router.post(
        "/api/sessions/{session_id}/chat",
        response_model=ChatResponse,
        tags=["agent"],
        summary="执行一轮 Agent 对话",
    )
    async def chat(session_id: str, body: ChatBody, request: Request):
        return await execute_turn(
            session_id, body.content, getattr(request.state, "request_id", None)
        )

    @router.post(
        "/api/sessions/{session_id}/chat/stream",
        tags=["agent"],
        summary="以 SSE 流式执行一轮 Agent 对话",
    )
    async def chat_stream(session_id: str, body: ChatBody, request: Request):
        if session_id in deleting_sessions:
            raise HTTPException(409, "该会话正在删除")
        if not store.session(session_id):
            raise HTTPException(404, "会话不存在")
        lock = session_locks.setdefault(session_id, asyncio.Lock())
        if lock.locked() or session_id in active_turns:
            raise HTTPException(409, "该会话已有一轮对话正在执行")
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        request_id = getattr(request.state, "request_id", None)
        started = False

        async def emit(event: dict[str, Any]):
            await queue.put(event)

        async def worker():
            nonlocal started
            started = True
            try:
                async with lock:
                    if session_id in deleting_sessions:
                        raise HTTPException(409, "该会话正在删除")
                    if not store.session(session_id):
                        raise HTTPException(404, "会话不存在")
                    message, memories, trace = await service.chat_with_trace(
                        session_id,
                        body.content.strip(),
                        emit,
                        request_id=request_id,
                    )
                await queue.put(
                    {
                        "type": "complete",
                        "message": message,
                        "memories_created": memories,
                        "trace": trace,
                    }
                )
            except asyncio.CancelledError:
                await queue.put({"type": "cancelled", "session_id": session_id})
                raise
            except Exception as exc:
                await queue.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            finally:
                release_turn(session_id, asyncio.current_task(), lock)
                await queue.put(None)

        def finished(done):
            release_turn(session_id, done, lock)
            # Cancellation before the coroutine starts never enters its finally.
            if not started and done.cancelled():
                queue.put_nowait({"type": "cancelled", "session_id": session_id})
                queue.put_nowait(None)

        with message_ingress("chat"):
            task = asyncio.create_task(worker())
        active_turns[session_id] = task
        task.add_done_callback(finished)

        async def events():
            try:
                while True:
                    if await request.is_disconnected():
                        task.cancel()
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    if event is None:
                        break
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                if not task.done():
                    task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post(
        "/api/sessions/{session_id}/cancel",
        response_model=CancelResponse,
        tags=["agent"],
        summary="取消正在执行的 Agent turn",
    )
    async def cancel_turn(session_id: str):
        task = active_turns.get(session_id)
        if not task or task.done():
            return {"status": "idle", "session_id": session_id}
        task.cancel()
        return {"status": "cancelled", "session_id": session_id}

    @router.delete(
        "/api/sessions/{session_id}", status_code=204, tags=["sessions"], summary="删除会话及其消息"
    )
    async def delete_session(session_id: str):
        if session_id in deleting_sessions:
            raise HTTPException(409, "该会话正在删除")
        deleting_sessions.add(session_id)
        lock = session_locks.setdefault(session_id, asyncio.Lock())
        try:
            task = active_turns.get(session_id)
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            async with lock:
                if not store.delete_session(session_id):
                    raise HTTPException(404, "会话不存在")
                service.runtime.memory.refresh_markdown()
            return Response(status_code=204)
        finally:
            deleting_sessions.discard(session_id)
            if not lock.locked() and session_id not in active_turns:
                session_locks.pop(session_id, None)

    return router
