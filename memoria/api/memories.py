"""Tenant-bound memories routes."""

from __future__ import annotations
from fastapi import APIRouter
from .context import TenantApiContext
from ..persistence.reviews import ReviewRepository
import asyncio
from typing import Literal
from fastapi import HTTPException, Query
from fastapi.responses import Response
from ..claims import ClaimError
from ..memory import export_memories_markdown
from ..store import MemorySourceUnavailable, MemoryVersionConflict
from .schemas import (
    MemoryBody,
    MemoryCorrectionBody,
    MemoryJobResponse,
    MemoryPatch,
    MemoryReindexResponse,
    MemoryReplacementResponse,
    MemoryResponse,
    MemoryReviewApproval,
    MemoryReviewProposal,
    MemoryReviewResponse,
    MemoryTimelineResponse,
    MemoryUndoBody,
    MemoryUndoResponse,
    MemoryWriteResponse,
)


def memories_router(context: TenantApiContext):
    router = APIRouter()
    service = context.service
    store = context.store
    reviews = ReviewRepository(store)

    @router.get(
        "/api/memories",
        response_model=list[MemoryResponse],
        tags=["memories"],
        summary="搜索长期记忆",
    )
    async def memories(
        q: str = Query(default="", max_length=200),
        limit: int = Query(default=100, ge=1, le=500),
        status: Literal["active", "superseded", "all"] = "active",
    ):
        if q.strip() and status == "active":
            return await service.kernel.recall(q, limit)
        return store.memories(q, limit, status)

    @router.get(
        "/api/memories/export.md", tags=["memories"], summary="导出人类可读 Markdown 记忆层"
    )
    def export_memories_md(limit: int = Query(default=500, ge=1, le=2000)):
        markdown = export_memories_markdown(store.retrieval_memories(limit))
        return Response(markdown, media_type="text/markdown; charset=utf-8")

    @router.get(
        "/api/sessions/{session_id}/compaction", tags=["sessions"], summary="查看会话压缩摘要"
    )
    def session_compaction(session_id: str):
        if not store.session(session_id):
            raise HTTPException(404, "会话不存在")
        item = store.session_compaction(session_id)
        if not item:
            return {
                "session_id": session_id,
                "summary": "",
                "covered_message_ids": [],
                "cursor_message_id": None,
                "updated_at": None,
            }
        return item

    @router.post(
        "/api/memories",
        response_model=MemoryWriteResponse,
        tags=["memories"],
        summary="创建、强化或替代长期记忆",
    )
    async def create_memory(body: MemoryBody):
        if not body.content.strip():
            raise HTTPException(422, "记忆内容不能为空")
        result = await service.runtime.memory.remember(
            body.content, body.kind, body.importance, "manual"
        )
        return result.public_dict()

    @router.post(
        "/api/memories/reindex",
        response_model=MemoryReindexResponse,
        tags=["memories"],
        summary="回填长期记忆语义向量",
    )
    async def reindex_memories(limit: int = Query(default=1000, ge=1, le=5000)):
        return await service.runtime.memory.reindex(limit)

    @router.get(
        "/api/memory-jobs",
        response_model=list[MemoryJobResponse],
        tags=["memories"],
        summary="查询后台记忆任务",
    )
    def memory_jobs(limit: int = Query(default=50, ge=1, le=200)):
        return store.memory_jobs(limit)

    @router.get(
        "/api/memory-reviews",
        response_model=list[MemoryReviewResponse],
        tags=["memories"],
        summary="查询自动提取记忆审核队列",
    )
    def memory_reviews(
        status: Literal["pending", "approved", "rejected", "all"] = "pending",
        limit: int = Query(default=100, ge=1, le=500),
    ):
        return store.memory_reviews(status, limit)

    def personal_review_source(message_id: str):
        source = store.message_source(message_id)
        if not source:
            raise HTTPException(409, "来源消息不可用，请重新核对原话")
        from ..claims import sha

        service.claims._evidence(
            {
                "evidence": [
                    {
                        "message_id": message_id,
                        "start": 0,
                        "end": len(source["content"]),
                        "quote": source["content"],
                        "content_sha256": sha(source["content"]),
                    }
                ]
            }
        )

    @router.post(
        "/api/memory-reviews/propose",
        response_model=MemoryReviewResponse,
        status_code=201,
        tags=["memories"],
    )
    def propose_memory_review(body: MemoryReviewProposal):
        with store._memory_transaction():
            personal_review_source(body.message_id)
            try:
                return store.propose_memory_review(
                    body.message_id, body.content, body.kind, body.importance
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

    @router.post(
        "/api/memory-reviews/{review_id}/approve",
        response_model=MemoryReviewResponse,
        tags=["memories"],
        summary="批准并写入候选记忆",
    )
    async def approve_memory_review(review_id: str, body: MemoryReviewApproval):
        if not body.content.strip():
            raise HTTPException(422, "记忆内容不能为空")
        candidate = store.memory_review(review_id)
        if not candidate:
            raise HTTPException(404, "候选记忆不存在")
        pattern_link = reviews.formation_pattern(review_id)
        if pattern_link:
            if body.claim is not None or (body.content.strip(), body.kind, body.importance) != (
                candidate["content"],
                candidate["kind"],
                candidate["importance"],
            ):
                raise HTTPException(
                    409,
                    "跨会话模式须保留完整证据组，不能直接改成单条原话Claim；请核对来源后重新整理",
                )
            return await service.auto_review.review(review_id, force=True)
        if not store.message_source(candidate["source_ref"]):
            raise HTTPException(409, "来源消息已删除，不能批准悬空记忆")
        review = store.claim_memory_review(review_id, body.content, body.kind, body.importance)
        if not review:
            raise HTTPException(409, "候选记忆已处理，请刷新审核队列")
        if not store.message_source(review["source_ref"]):
            store.reset_memory_review(review_id)
            raise HTTPException(409, "来源消息已删除，不能批准悬空记忆")
        try:
            with store._memory_transaction():
                personal_review_source(review["source_ref"])
            if body.claim is not None:
                from ..memory.review import approve_review_with_claim

                return await approve_review_with_claim(service, review, body)
            result = await service.runtime.memory.remember(
                body.content,
                body.kind,
                body.importance,
                "reviewed_conversation",
                review["source_ref"],
                require_source=True,
            )
            store.finish_memory_review(
                review_id, result.memory["id"] if result.memory else None, result.action
            )
        except ClaimError:
            store.reset_memory_review(review_id)
            raise
        except MemoryVersionConflict:
            store.reset_memory_review(review_id)
            raise
        except MemorySourceUnavailable as exc:
            store.reset_memory_review(review_id)
            raise HTTPException(409, "来源消息已删除，不能批准悬空记忆") from exc
        except BaseException as exc:
            store.reset_memory_review(review_id)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise HTTPException(502, "记忆写入失败，候选项已恢复待审核状态") from exc
        return store.memory_review(review_id)

    @router.post(
        "/api/memory-reviews/{review_id}/auto-review",
        response_model=MemoryReviewResponse,
        tags=["memories"],
    )
    async def auto_review_memory(review_id: str, body: MemoryReviewApproval | None = None):
        if body is not None:
            if body.claim is not None:
                raise HTTPException(422, "自动审核从原话判断结构化字段；不要混入手工Claim")
            if not body.content.strip():
                raise HTTPException(422, "候选不能为空白")
            with store._memory_transaction():
                row = store.memory_review(review_id)
                if not row:
                    raise HTTPException(404, "候选不存在")
                personal_review_source(row["source_ref"])
                if not reviews.edit_pending(
                    review_id, body.content.strip(), body.kind, body.importance
                ):
                    raise HTTPException(409, "候选已处理，请刷新")
        return await service.auto_review.review(review_id, force=True)

    @router.post(
        "/api/messages/{message_id}/memory-extraction",
        response_model=MemoryJobResponse,
        tags=["memories"],
    )
    def extract_history_memory(message_id: str):
        with store._memory_transaction():
            personal_review_source(message_id)
            return store.request_memory_extraction(message_id)

    @router.post(
        "/api/memory-reviews/{review_id}/reject",
        response_model=MemoryReviewResponse,
        tags=["memories"],
        summary="拒绝候选记忆",
    )
    def reject_memory_review(review_id: str):
        if not store.memory_review(review_id):
            raise HTTPException(404, "候选记忆不存在")
        if not store.reject_memory_review(review_id):
            raise HTTPException(409, "候选记忆已处理，请刷新审核队列")
        return store.memory_review(review_id)

    @router.post(
        "/api/memory-jobs/{job_id}/retry",
        response_model=MemoryJobResponse,
        tags=["memories"],
        summary="重试失败的记忆任务",
    )
    def retry_memory_job(job_id: str):
        if not store.retry_memory_job(job_id):
            raise HTTPException(409, "只有 failed 任务可以手动重试")
        return store.memory_job(job_id)

    @router.post(
        "/api/memories/undo",
        response_model=MemoryUndoResponse,
        tags=["memories"],
        summary="按消息来源撤销自动记忆",
    )
    def undo_memories(body: MemoryUndoBody):
        result = store.undo_memory_sources(body.source_refs, body.dry_run)
        if not body.dry_run:
            service.runtime.memory.refresh_markdown()
        return result

    @router.get(
        "/api/memories/{memory_id}/history",
        response_model=list[MemoryReplacementResponse],
        tags=["memories"],
        summary="查询记忆替代历史",
    )
    def memory_history(memory_id: str):
        if not store.memory(memory_id):
            raise HTTPException(404, "记忆不存在")
        return store.memory_history(memory_id)

    @router.get(
        "/api/memories/{memory_id}/timeline",
        response_model=list[MemoryTimelineResponse],
        tags=["memories"],
        summary="查询完整记忆版本时间线",
    )
    def memory_timeline(memory_id: str):
        if not store.memory(memory_id):
            raise HTTPException(404, "记忆不存在")
        return store.memory_timeline(memory_id)

    @router.post(
        "/api/memories/{memory_id}/correct",
        response_model=MemoryResponse,
        tags=["memories"],
        summary="纠正记忆并保留旧版本",
    )
    async def correct_memory(memory_id: str, body: MemoryCorrectionBody):
        if not body.content.strip() or not body.reason.strip():
            raise HTTPException(422, "纠正内容和原因不能为空")
        current = store.memory(memory_id)
        if not current:
            raise HTTPException(404, "记忆不存在")
        if current["status"] != "active":
            raise HTTPException(409, "只能纠正当前有效版本，请刷新记忆列表")
        try:
            item = await service.runtime.memory.correct(
                memory_id, body.content, body.kind, body.importance, body.reason
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not item:
            raise HTTPException(409, "记忆已被其他操作替代，请刷新记忆列表")
        return item

    @router.patch(
        "/api/memories/{memory_id}",
        response_model=MemoryResponse,
        tags=["memories"],
        summary="编辑长期记忆并保留旧版本",
    )
    async def update_memory(memory_id: str, body: MemoryPatch):
        changes = body.model_dump(exclude_unset=True, exclude_none=True)
        if not changes:
            raise HTTPException(422, "至少提供一个修改字段")
        current = store.memory(memory_id)
        if not current:
            raise HTTPException(404, "记忆不存在")
        if current["status"] != "active":
            raise HTTPException(409, "只能编辑当前有效版本，请刷新记忆列表")
        try:
            item = await service.runtime.memory.correct(
                memory_id,
                changes.get("content", current["content"]),
                changes.get("kind", current["kind"]),
                changes.get("importance", current["importance"]),
                "用户通过 PATCH 编辑",
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not item:
            raise HTTPException(409, "记忆已被其他操作替代，请刷新记忆列表")
        return item

    @router.delete(
        "/api/memories/{memory_id}", status_code=204, tags=["memories"], summary="删除长期记忆"
    )
    def delete_memory(memory_id: str):
        if not store.delete_memory(memory_id):
            raise HTTPException(404, "记忆不存在")
        service.runtime.memory.refresh_markdown()
        return Response(status_code=204)

    return router
