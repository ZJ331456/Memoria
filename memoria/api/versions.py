"""Historical and evolution routes; URI contracts retained."""

from fastapi import APIRouter
from fastapi import HTTPException
from ..store import normalize_time
from .schemas import EvolveBody


def versions_router(context):
    app = context.app
    router = APIRouter()
    from fastapi import Query as _Q

    @router.get(
        "/api/memories/time-travel", tags=["memories"], summary="时间旅行查询某时刻有效记忆"
    )
    async def time_travel(
        as_of: str = _Q(min_length=4, max_length=40),
        q: str = _Q(default="", max_length=200),
        limit: int = _Q(default=20, ge=1, le=200),
    ):
        try:
            timestamp = normalize_time(as_of)
        except ValueError as exc:
            raise HTTPException(422, "as_of 必须为 ISO 8601 时间") from exc
        if q.strip():
            return await app.state.service.kernel.recall(q, limit=limit, as_of=timestamp)
        return [
            {k: v for k, v in item.items() if k != "embedding"}
            for item in app.state.store.time_travel(timestamp, limit)
        ]

    @router.get("/api/memories/{memory_id}", tags=["memories"], summary="记忆详情(含邻居与演化链)")
    def memory_detail(memory_id: str):
        store = app.state.store
        item = store.memory(memory_id)
        if not item:
            raise HTTPException(status_code=404, detail="memory not found")
        item["neighbors"] = store.memory_neighbors(memory_id, depth=1, limit=10)
        item["evolutions"] = store.memory_evolutions(memory_id)
        item["validity_intervals"] = store.memory_validity(memory_id)
        item.pop("embedding", None)
        return item

    @router.get(
        "/api/memories/{memory_id}/neighbors", tags=["memories"], summary="记忆图邻居(一跳/多跳)"
    )
    def neighbors(memory_id: str, depth: int = _Q(default=1, ge=1, le=3)):
        store = app.state.store
        if not store.memory(memory_id):
            from fastapi import HTTPException as _H

            raise _H(404, "记忆不存在")
        return store.memory_neighbors(memory_id, depth)

    @router.post(
        "/api/memories/{memory_id}/links/{other_id}", tags=["memories"], summary="建立记忆交叉链接"
    )
    def add_link(
        memory_id: str, other_id: str, relation: str = _Q(default="related", max_length=40)
    ):
        store = app.state.store
        item = store.add_memory_link(memory_id, other_id, relation)
        if not item:
            from fastapi import HTTPException as _H

            raise _H(422, "无法建立链接")
        return item

    @router.post(
        "/api/memories/{memory_id}/evolve", tags=["memories"], summary="记忆持续演化(refinement)"
    )
    async def evolve(memory_id: str, body: EvolveBody):
        service = app.state.service
        item = await service.runtime.memory.evolve(memory_id, body.new_info, body.reason)
        if not item:
            from fastapi import HTTPException as _H

            raise _H(404, "记忆不存在或非有效版本")
        return item

    @router.get("/api/memories/{memory_id}/evolutions", tags=["memories"], summary="记忆演化历史")
    def evolutions(memory_id: str):
        return app.state.store.memory_evolutions(memory_id)

    return router
