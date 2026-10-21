"""Tenant-bound status routes."""

from __future__ import annotations
from fastapi import APIRouter
from .context import TenantApiContext
from typing import Any
from fastapi import HTTPException, Query
from fastapi.responses import Response
from .schemas import TraceResponse


def status_router(context: TenantApiContext):
    router = APIRouter()
    metrics = context.metrics
    service = context.service
    settings = context.settings
    store = context.store

    @router.get("/metrics", include_in_schema=False)
    def prometheus_metrics():
        if not settings.metrics_enabled:
            raise HTTPException(404, "metrics disabled")
        return Response(metrics.render(store), media_type="text/plain; version=0.0.4")

    @router.get("/api/overview", tags=["system"], summary="获取 Dashboard 总览")
    def overview() -> dict[str, Any]:
        skills = [item.public_dict() for item in service.skills.list()] if service.skills else []
        return {
            **store.overview(),
            "models": settings.public_dict(),
            "vector_index": store.vector_index_status,
            "tools": service.runtime.tools.catalog(),
            "tool_search": service.tool_presentation.public_status(),
            "mcp": service.mcp.status(),
            "drift": service.drift.status(),
            "pipeline": service.runtime.pipeline.inspect(),
            "skills": skills,
            "markdown": service.markdown.status(),
            "memory_layers": {
                "config": (
                    settings.memory_layers.public_dict()
                    if settings.memory_layers
                    else {"enabled": False}
                ),
                "episodic": service.episodes.stats() if service.episodes else {"enabled": False},
            },
            "setup": service.models_public(),
        }

    @router.get(
        "/api/traces",
        response_model=list[TraceResponse],
        tags=["traces"],
        summary="查询最近运行追踪",
    )
    def traces(
        session_id: str = Query(default="", max_length=64),
        limit: int = Query(default=50, ge=1, le=200),
    ):
        return store.traces(session_id, limit)

    return router
