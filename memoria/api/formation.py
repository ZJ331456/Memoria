"""Tenant-bound formation routes."""

from __future__ import annotations
from fastapi import APIRouter
from .context import TenantApiContext
from ..persistence.formation import FormationRepository
from ..persistence.studio import ProjectionError
from fastapi import HTTPException, Query


def formation_router(context: TenantApiContext):
    router = APIRouter()
    service = context.service
    settings = context.settings
    store = context.store
    repository = FormationRepository(store)

    @router.get(
        "/api/memory-formation",
        tags=["memory layers"],
        summary="当前账号观察、模式及形成策略（只读）",
    )
    def formation_status():
        return service.formation.status() if service.formation else {"enabled": False}

    @router.get(
        "/api/memory-observations",
        tags=["memory layers"],
        summary="当前账号有来源支持的观察（不是批准事实）",
    )
    def formation_observations(limit: int = Query(default=50, ge=1, le=200)):
        try:
            return repository.formation_observations(limit)
        except ProjectionError as exc:
            raise HTTPException(exc.status, exc.detail) from exc

    @router.get(
        "/api/memory-extraction-diagnostics",
        tags=["memory layers"],
        summary="当前有效来源的提取覆盖与逐项失败，不是语义召回率",
    )
    def extraction_diagnostics(
        limit: int = Query(default=20, ge=1, le=100), session_id: str | None = None
    ):
        from ..memory.extraction import visible

        if session_id and not store.session(session_id):
            raise HTTPException(404, "会话不存在")
        with store.lock:
            return visible(store, session_id=session_id, limit=limit)

    @router.get(
        "/api/memory-patterns",
        tags=["memory layers"],
        summary="当前账号可核对的跨会话模式与演化版本",
    )
    def formation_patterns(limit: int = Query(default=50, ge=1, le=200)):
        try:
            return repository.formation_patterns(limit)
        except ProjectionError as exc:
            raise HTTPException(exc.status, exc.detail) from exc

    @router.get(
        "/api/memory-layers", tags=["memory layers"], summary="查看五层记忆配置与任务情节记忆统计"
    )
    def memory_layers():
        return {
            "config": (
                settings.memory_layers.public_dict()
                if settings.memory_layers
                else {"enabled": False}
            ),
            "episodic": service.episodes.stats() if service.episodes else {"enabled": False},
        }

    @router.get("/api/datasets", tags=["sessions"], summary="当前用户的真实语料及来源清单")
    def list_datasets():
        try:
            return repository.list_datasets()
        except ProjectionError as exc:
            raise HTTPException(exc.status, exc.detail) from exc

    @router.get(
        "/api/datasets/{dataset_id}", tags=["sessions"], summary="当前用户语料的原文 ID 与消息映射"
    )
    def dataset_receipt(dataset_id: str):
        try:
            return repository.dataset_receipt(dataset_id)
        except ProjectionError as exc:
            raise HTTPException(exc.status, exc.detail) from exc

    return router
