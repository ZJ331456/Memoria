"""Tenant-bound integrations routes."""

from __future__ import annotations
from fastapi import APIRouter
from .context import TenantApiContext
from typing import Any
from fastapi import HTTPException, Query
from .schemas import (
    DriftRunBody,
    MarkdownWriteBody,
    ModelTestBody,
    ModelsUpdateBody,
    ToolExecuteBody,
    ToolExecuteResponse,
    ToolSearchBody,
)


def integrations_router(context: TenantApiContext):
    router = APIRouter()
    service = context.service

    @router.get("/api/setup/status", tags=["setup"], summary="Setup 向导状态")
    def setup_status():
        return service.models_public()

    @router.get("/api/settings/models", tags=["setup"], summary="读取脱敏后的模型配置")
    def get_models():
        return service.models_public()

    @router.put("/api/settings/models", tags=["setup"], summary="热更新模型配置（不回显密钥）")
    def put_models(body: ModelsUpdateBody):
        updates = {
            key: value
            for key, value in body.model_dump(exclude_none=True).items()
            if isinstance(value, dict) and value
        }
        if not updates:
            raise HTTPException(422, "至少提供一个模型槽位")
        return service.update_models(updates)

    @router.post("/api/settings/models/test", tags=["setup"], summary="测试模型连通性")
    async def test_models(body: ModelTestBody):
        return await service.test_model(body.slot)

    @router.get("/api/markdown", tags=["markdown"], summary="Markdown 双层状态")
    def markdown_status():
        return service.markdown.status()

    @router.get("/api/markdown/{name}", tags=["markdown"], summary="读取 MEMORY/SELF/PENDING")
    def read_markdown(name: str):
        try:
            content = service.markdown.read(name)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {
            "name": name.upper() if name.lower() in {"memory", "self", "pending"} else name,
            "content": content,
        }

    @router.put("/api/markdown/{name}", tags=["markdown"], summary="写入 SELF 或 PENDING")
    def write_markdown(name: str, body: MarkdownWriteBody):
        try:
            content = service.markdown.write(name, body.content)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"name": name, "content": content}

    @router.post(
        "/api/markdown/MEMORY/sync", tags=["markdown"], summary="从结构化记忆重写 MEMORY.md"
    )
    def sync_memory_markdown():
        content = service.runtime.memory.refresh_markdown()
        return {"name": "MEMORY", "content": content, "status": service.markdown.status()}

    @router.get("/api/tools", tags=["tools"], summary="列出模型可调用工具")
    def tools() -> list[dict[str, Any]]:
        return service.runtime.tools.catalog()

    @router.get("/api/tools/search", tags=["tools"], summary="Tool Search 状态与目录")
    def tool_search_status():
        return service.tool_presentation.public_status()

    @router.post("/api/tools/search", tags=["tools"], summary="调试执行 tool_search")
    async def tool_search_run(body: ToolSearchBody):
        return service.tool_presentation.search(
            body.query,
            top_k=body.top_k,
            allowed_risk=body.allowed_risk,
        )

    @router.get("/api/mcp", tags=["mcp"], summary="MCP server 连接状态")
    def mcp_status():
        return {**service.mcp.status(), "startup": service.integration_status()}

    @router.post("/api/mcp/reload", tags=["mcp"], summary="重新加载并连接 MCP servers")
    async def mcp_reload():
        return await service.reload_mcp()

    @router.get("/api/drift", tags=["drift"], summary="Drift 调度状态与最近运行")
    def drift_status(limit: int = Query(default=20, ge=1, le=100)):
        return {"status": service.drift.status(), "runs": service.drift.recent_runs(limit)}

    @router.post("/api/drift/run", tags=["drift"], summary="手动触发一轮 Drift")
    async def drift_run(body: DriftRunBody):
        result = await service.drift.maybe_run(trigger="manual", force=body.force)
        return {"result": result, "status": service.drift.status()}

    @router.get("/api/skills", tags=["skills"], summary="列出已发现的轻量技能")
    def list_skills():
        if not service.skills:
            return []
        return [item.public_dict() for item in service.skills.list()]

    @router.get("/api/skills/{skill_name}", tags=["skills"], summary="读取技能正文")
    def get_skill(skill_name: str):
        if not service.skills:
            raise HTTPException(404, "skills 未启用")
        record = service.skills.get(skill_name)
        if not record:
            raise HTTPException(404, "技能不存在")
        return {
            **record.public_dict(),
            "body": record.body,
            "root_dir": str(record.root_dir) if record.root_dir else "",
        }

    @router.post("/api/skills/reload", tags=["skills"], summary="重新扫描 skills 目录")
    def reload_skills():
        if not service.skills:
            raise HTTPException(404, "skills 未启用")
        return [item.public_dict() for item in service.skills.reload()]

    @router.post(
        "/api/tools/{tool_name}/execute",
        response_model=ToolExecuteResponse,
        tags=["tools"],
        summary="调试执行一个工具",
    )
    async def execute_tool(tool_name: str, body: ToolExecuteBody):
        catalog = {item["name"]: item for item in service.runtime.tools.catalog()}
        tool = catalog.get(tool_name)
        if not tool:
            raise HTTPException(404, "工具不存在")
        if tool["risk"] != "read-only" and not body.confirm_write:
            raise HTTPException(409, "写工具需要 confirm_write=true")
        return await service.runtime.tools.execute(
            tool_name, body.arguments, allow_write=body.confirm_write
        )

    return router
