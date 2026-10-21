"""Tenant-bound episodes routes."""

from __future__ import annotations
from fastapi import APIRouter
from .context import TenantApiContext
from typing import Literal
from fastapi import HTTPException, Query
from .schemas import EpisodeMaintenanceBody, EpisodePatch


def episodes_router(context: TenantApiContext):
    router = APIRouter()
    service = context.service

    def episode_manager():
        if not service.episodes:
            raise HTTPException(409, "任务情节记忆未启用")
        return service.episodes

    @router.get("/api/episodes", tags=["memory layers"], summary="检索有来源的任务执行记录")
    def list_episodes(
        query: str = Query(default="", max_length=300),
        status: Literal["active", "archive", "all"] = "active",
        limit: int = Query(default=100, ge=1, le=500),
    ):
        episodes = episode_manager()
        if query.strip():
            if status != "active":
                raise HTTPException(422, "关键词检索仅支持 active 记录；归档记录请使用列表")
            return episodes.search(query, limit)
        return episodes.list(limit, status)

    @router.post(
        "/api/episodes/maintenance", tags=["memory layers"], summary="预览或执行任务情节记忆软归档"
    )
    def maintain_episodes(body: EpisodeMaintenanceBody = EpisodeMaintenanceBody()):
        return episode_manager().maintenance(dry_run=body.dry_run)

    @router.get(
        "/api/episodes/{episode_id}",
        tags=["memory layers"],
        summary="查看任务执行记录与来源消息 ID",
    )
    def episode_detail(episode_id: str):
        item = episode_manager().detail(episode_id, include_expired=True)
        if not item:
            raise HTTPException(404, "任务执行记录不存在或来源已删除")
        return item

    @router.patch(
        "/api/episodes/{episode_id}", tags=["memory layers"], summary="固定或软归档任务执行记录"
    )
    def update_episode(episode_id: str, body: EpisodePatch):
        episodes = episode_manager()
        item = (
            episodes.pin(episode_id, body.pin)
            if "pin" in body.model_fields_set
            else episodes.archive(episode_id, body.reason or "")
        )
        if not item:
            raise HTTPException(404, "任务执行记录不存在或已归档")
        return item

    return router
