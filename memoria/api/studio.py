"""Read-only console HTTP contracts. SQL belongs to StudioRepository."""

from typing import Literal
from fastapi import APIRouter, HTTPException, Query, Request
from ..persistence.studio import (
    StudioRepository,
    ProjectionError,
    literal_pattern,
    readable_text,
    excerpt,
)


def studio_router(store, claims, service=None):
    router = APIRouter(prefix="/api/studio", tags=["studio"])
    repository = StudioRepository(store, claims.user_id) if store is not None else None

    def ready():
        if repository is None:
            raise HTTPException(503, "Console is unavailable")

    def query(name, *args):
        ready()
        try:
            return getattr(repository, name)(*args)
        except ProjectionError as exc:
            raise HTTPException(exc.status, exc.detail) from exc

    @router.get("/summary")
    def summary():
        return query("summary")

    @router.get("/conversations")
    def conversations(
        q: str = Query(default="", max_length=200),
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
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ):
        return query("conversations", q, origin, sort, page, page_size)

    @router.get("/conversations/{session_id}/memory-status")
    def memory_status(session_id: str, ids: str = Query(default="", max_length=6600)):
        return query("memory_status", session_id, ids)

    @router.get("/pipeline")
    def pipeline(request: Request):
        ready()
        (
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
        ) = query("pipeline_snapshot")
        task = getattr(request.app.state, "memory_worker_task", None)
        worker_state = (
            "unavailable"
            if task is None
            else (
                "stopped"
                if task.cancelled()
                else (
                    "failed"
                    if task.done() and task.exception()
                    else "stopped" if task.done() else "running"
                )
            )
        )
        reasons = []
        if imported:
            reasons.append(
                {
                    "code": "public_history",
                    "text": "导入的公开资料保留为实验来源，不自动变成本人的长期事实。",
                }
            )
        if legacy:
            reasons.append(
                {
                    "code": "legacy_outcome_unknown",
                    "text": "部分旧任务只有完成状态，没有详细抽取计数；未见候选不能直接判定模型返回了空数组。",
                }
            )
        if known_empty:
            reasons.append(
                {
                    "code": "empty_extraction",
                    "text": "已有任务明确返回0条长期记忆候选；普通提问不一定包含值得长期保存的事实。",
                }
            )
        if not review_counts.get("pending", 0):
            reasons.append(
                {
                    "code": "no_pending_review",
                    "text": "当前没有待处理候选。可从本人原话启动LLM提取与审核；临时提问可能不产生长期记忆。",
                }
            )
        if counts["memories"] == 0:
            reasons.append(
                {
                    "code": "no_active_memory",
                    "text": "尚无有效长期记忆。对话保存、候选提取和审核入库是不同步骤。",
                }
            )
        models, integrations = ({}, [])
        if service is not None:
            for role, config in [
                ("main", service.settings.main),
                ("fast", service.settings.fast),
                ("embedding", service.settings.embedding),
            ]:
                models[role] = {
                    "model": config.model,
                    "ready": config.is_ready(role="embedding" if role == "embedding" else "llm"),
                }
            integrations = [
                {
                    "name": s["name"],
                    "enabled": s["enabled"],
                    "connected": s["connected"],
                    "tool_count": len(s["tools"]),
                    "has_error": bool(s["error"]),
                }
                for s in service.mcp.status()["servers"]
            ]
        return {
            "counts": {
                **counts,
                "imported_conversations": len(imported),
                "native_conversations": counts["sessions"] - len(imported),
                "eligible_personal_messages": eligible,
                "reviews": review_counts,
                "claims": claim_statuses,
            },
            "extraction": {
                "statuses": job_counts,
                "known_empty": known_empty,
                "legacy_outcome_unknown": legacy,
                "recent": recent,
                "worker": worker_state,
                "recent_limit": 50,
            },
            "indexes": indexes,
            "models": models,
            "integrations": integrations,
            "integration_startup": (
                service.integration_status() if service is not None else {"state": "unknown"}
            ),
            "approval_policy": {
                "mode": service.settings.memory_approval_mode if service else "manual",
                "default": (
                    "低风险模型审核后自动入库；敏感、冲突和约束不明转人工辅助"
                    if service and service.settings.memory_approval_mode == "llm"
                    else "已配置为人工审核；可明确请求单次LLM辅助"
                ),
            },
            "reasons": reasons,
            "scope": "current_authenticated_user",
            "model_called": False,
        }

    @router.get("/conversations/{session_id}/messages")
    def messages(
        session_id: str,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=100),
        q: str = Query(default="", max_length=200),
        anchor_id: str = Query(default="", max_length=128),
    ):
        return query("messages", session_id, page, page_size, q, anchor_id)

    @router.get("/memories")
    def memories(
        q: str = Query(default="", max_length=200),
        status: Literal["active", "superseded", "all"] = "active",
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=100),
    ):
        return query("memories", q, status, page, page_size)

    return router


__all__ = ["studio_router", "literal_pattern", "readable_text", "excerpt"]
