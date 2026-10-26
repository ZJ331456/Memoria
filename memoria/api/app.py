"""Tenant application composition, middleware and lifecycle; routes live in focused modules."""

from __future__ import annotations
import asyncio
import re
import time
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from ..config import Settings
from ..claims import ClaimError
from ..governance import GovernanceError, MemoryGovernance
from ..llm import LLMClient
from ..observability import MetricRegistry, RequestContext
from ..security import RequestGate
from ..service import AgentService
from ..store import MemoryVersionConflict, Store
from .governance import governance_router
from .claims import claims_router
from .research_runs import research_runs_router
from .studio import studio_router
from .schemas import (
    CancelResponse as CancelResponse,
    ChatBody as ChatBody,
    ChatResponse as ChatResponse,
    DriftRunBody as DriftRunBody,
    EpisodeMaintenanceBody as EpisodeMaintenanceBody,
    EpisodePatch as EpisodePatch,
    ErrorResponse as ErrorResponse,
    EvolveBody as EvolveBody,
    HealthResponse as HealthResponse,
    MarkdownWriteBody as MarkdownWriteBody,
    MemoryBody as MemoryBody,
    MemoryCorrectionBody as MemoryCorrectionBody,
    MemoryJobResponse as MemoryJobResponse,
    MemoryPatch as MemoryPatch,
    MemoryReindexResponse as MemoryReindexResponse,
    MemoryReplacementResponse as MemoryReplacementResponse,
    MemoryResponse as MemoryResponse,
    MemoryReviewApproval as MemoryReviewApproval,
    MemoryReviewProposal as MemoryReviewProposal,
    MemoryReviewResponse as MemoryReviewResponse,
    MemoryTimelineResponse as MemoryTimelineResponse,
    MemoryUndoBody as MemoryUndoBody,
    MemoryUndoResponse as MemoryUndoResponse,
    MemoryWriteResponse as MemoryWriteResponse,
    MessageResponse as MessageResponse,
    MessageSourceResponse as MessageSourceResponse,
    ModelSlotBody as ModelSlotBody,
    ModelTestBody as ModelTestBody,
    ModelsUpdateBody as ModelsUpdateBody,
    SessionBody as SessionBody,
    SessionPatch as SessionPatch,
    SessionResponse as SessionResponse,
    StrictModel as StrictModel,
    ToolExecuteBody as ToolExecuteBody,
    ToolExecuteResponse as ToolExecuteResponse,
    ToolSearchBody as ToolSearchBody,
    TraceResponse as TraceResponse,
)
from .app_metadata import VERSION
from .context import TenantApiContext
from .status import status_router
from .formation import formation_router
from .episodes import episodes_router
from .integrations import integrations_router
from .conversations import conversations_router
from .memories import memories_router
from .versions import versions_router

MemoryKind = Literal["fact", "preference", "profile", "goal", "procedure"]


TAGS = [
    {"name": "system", "description": "健康检查、运行时能力和脱敏配置。"},
    {"name": "setup", "description": "模型热配置、连通性测试与 Setup 向导。"},
    {"name": "sessions", "description": "会话生命周期和消息历史。"},
    {"name": "agent", "description": "执行完整 Agent turn，包括记忆召回、工具循环和 trace。"},
    {"name": "memories", "description": "长期记忆查询、创建、编辑和删除。"},
    {"name": "memory layers", "description": "分层预算、任务情节记忆与遗忘维护。"},
    {"name": "markdown", "description": "MEMORY/SELF/PENDING Markdown 双层记忆。"},
    {"name": "tools", "description": "工具目录、Tool Search 与受风险级别保护的调试执行。"},
    {"name": "mcp", "description": "外部 MCP server 连接状态与热重载。"},
    {"name": "drift", "description": "空闲 Drift 调度、手动触发与运行审计。"},
    {"name": "skills", "description": "轻量 SKILL.md 目录发现、读取与热重载。"},
    {"name": "traces", "description": "每轮推理的耗时、召回与工具调用诊断。"},
    {"name": "governance", "description": "本机治理管理员：Agent 凭证与受审导入。"},
    {"name": "shared memory", "description": "基于 Agent key 和空间角色授权的共享记忆。"},
]


def _create_tenant_app(
    config_path: str | Path | None = None,
    *,
    tenant_settings: Settings | None = None,
    schema_only: bool = False,
    tenant_user_id: str = "local",
) -> FastAPI:
    """Internal runtime factory. Network entry points use the authenticated gateway."""
    settings = tenant_settings or Settings.load(config_path)
    store = None if schema_only else Store(settings.database, settings.vector_backend)
    governance = None if schema_only else MemoryGovernance(store)
    service = (
        None
        if schema_only
        else AgentService(settings, store, LLMClient(settings), user_id=tenant_user_id)
    )
    kernel = service.kernel if service is not None else None
    session_locks: dict[str, asyncio.Lock] = {}
    active_turns: dict[str, asyncio.Task] = {}
    deleting_sessions: set[str] = set()
    request_gate = RequestGate(settings)
    metrics = MetricRegistry()
    if service:
        service.set_busy_check(lambda: bool(active_turns))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if schema_only:
            raise RuntimeError("Schema-only application cannot serve requests")
        service.start_background_integrations()
        memory_task = asyncio.create_task(service.memory_worker.run(), name="memoria-memory-worker")
        app.state.memory_worker_task = memory_task
        drift_task = asyncio.create_task(service.drift.run_loop(), name="memoria-drift-worker")
        forgetting_task = (
            asyncio.create_task(
                service.forgetting_worker.run_loop(), name="memoria-forgetting-worker"
            )
            if service.forgetting_worker
            else None
        )
        try:
            yield
        finally:
            if forgetting_task:
                forgetting_task.cancel()
            drift_task.cancel()
            memory_task.cancel()
            if forgetting_task:
                with suppress(asyncio.CancelledError):
                    await forgetting_task
            with suppress(asyncio.CancelledError):
                await drift_task
            with suppress(asyncio.CancelledError):
                await memory_task
            for task in active_turns.values():
                task.cancel()
            if active_turns:
                await asyncio.gather(*active_turns.values(), return_exceptions=True)
            await service.stop_integrations()
            store.close()

    app = FastAPI(
        title="Memoria Agent API",
        version=VERSION,
        summary="可审核的个人与共享记忆、工具循环和运行追踪 API",
        description="API 默认挂载在 `/api`。交互式文档：`/docs`；OpenAPI JSON：`/openapi.json`。",
        openapi_tags=TAGS,
        lifespan=lifespan,
        responses={422: {"model": ErrorResponse, "description": "请求校验失败"}},
    )
    app.state.settings, app.state.store, app.state.service, app.state.metrics = (
        settings,
        store,
        service,
        metrics,
    )
    app.state.governance = governance
    app.include_router(claims_router(kernel))
    app.include_router(studio_router(store, service.claims if service else None, service))
    experiment_owner = settings.database.parent.parent
    app.include_router(
        research_runs_router(
            experiment_owner / "experiments" if experiment_owner.name == tenant_user_id else None,
            tenant_user_id,
        )
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID", "Authorization", "X-API-Key", "X-Agent-Key"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        started = time.perf_counter()
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        with RequestContext(request_id=request_id):
            denied = request_gate.check(request)
            if schema_only and request.url.path not in {
                "/api/health", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"
            }:
                denied = (503, "unavailable", "Schema-only application cannot serve tenant requests", {})
            if denied:
                status, code, message, headers = denied
                response = _error(request, status, code, message)
                response.headers.update(headers)
            else:
                response = await call_next(request)
        duration = time.perf_counter() - started
        route = getattr(request.scope.get("route"), "path", None) or re.sub(
            r"/[A-Fa-f0-9-]{16,}(?=/|$)", "/{id}", request.url.path
        )
        metrics.observe_http(request.method, route, response.status_code, duration)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Process-Time-Ms"] = str(int(duration * 1000))
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return _error(request, 422, "validation_error", details)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return _error(request, exc.status_code, _error_code(exc.status_code), str(exc.detail))

    @app.exception_handler(GovernanceError)
    async def governance_error(request: Request, exc: GovernanceError):
        return _error(request, exc.status_code, _error_code(exc.status_code), exc.detail)

    @app.exception_handler(MemoryVersionConflict)
    async def memory_conflict(request: Request, exc: MemoryVersionConflict):
        return _error(request, 409, "conflict", str(exc))

    @app.exception_handler(ClaimError)
    async def claim_error(request: Request, exc: ClaimError):
        return _error(request, exc.status, _error_code(exc.status), exc.detail)

    from ..memory.library import LibraryError
    from .library import library_router

    @app.exception_handler(LibraryError)
    async def library_error(request: Request, exc: LibraryError):
        return _error(request, exc.status, _error_code(exc.status), exc.detail)

    app.include_router(library_router(kernel))
    from .memory_kernel import memory_kernel_router

    app.include_router(memory_kernel_router(kernel))
    from .document_import import document_import_router

    app.include_router(document_import_router(kernel))

    from ..projects import ProjectError
    from .projects import projects_router

    app.include_router(projects_router(service.projects if service else None))

    @app.exception_handler(ProjectError)
    async def project_error(request: Request, exc: ProjectError):
        return _error(request, exc.status, _error_code(exc.status), exc.detail)

    @app.get("/api/health", response_model=HealthResponse, tags=["system"], summary="存活检查")
    def health() -> HealthResponse:
        return HealthResponse(version=VERSION)

    context = TenantApiContext(
        app,
        settings,
        store,
        service,
        governance,
        metrics,
        session_locks,
        active_turns,
        deleting_sessions,
        request_gate,
    )
    app.include_router(status_router(context))
    app.include_router(formation_router(context))
    app.include_router(episodes_router(context))
    app.include_router(integrations_router(context))
    app.include_router(conversations_router(context))
    app.include_router(memories_router(context))

    app.include_router(governance_router(governance, store, settings))
    app.include_router(versions_router(context))

    @app.get("/", include_in_schema=False)
    def service_index():
        return {
            "service": "Memoria-Agent",
            "docs": "/docs",
            "health": "/api/health",
            "memories": "/api/memories",
        }

    return app


def _error(request: Request, status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=ErrorResponse(
            code=code, message=message, request_id=getattr(request.state, "request_id", "")
        ).model_dump(),
    )


def _error_code(status: int) -> str:
    return {
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        413: "payload_too_large",
        422: "validation_error",
        429: "rate_limited",
        502: "upstream_error",
    }.get(status, "request_error")


# ---- 第十三轮: Bi-temporal + Agentic Memory 路由(追加,避免改动原有路由) ----

# Existing Python contract imports refer to the one schemas module.
__all__ = [
    "VERSION",
    "_create_tenant_app",
    "CancelResponse",
    "ChatBody",
    "ChatResponse",
    "DriftRunBody",
    "EpisodeMaintenanceBody",
    "EpisodePatch",
    "ErrorResponse",
    "EvolveBody",
    "HealthResponse",
    "MarkdownWriteBody",
    "MemoryBody",
    "MemoryCorrectionBody",
    "MemoryJobResponse",
    "MemoryPatch",
    "MemoryReindexResponse",
    "MemoryReplacementResponse",
    "MemoryResponse",
    "MemoryReviewApproval",
    "MemoryReviewProposal",
    "MemoryReviewResponse",
    "MemoryTimelineResponse",
    "MemoryUndoBody",
    "MemoryUndoResponse",
    "MemoryWriteResponse",
    "MessageResponse",
    "MessageSourceResponse",
    "ModelSlotBody",
    "ModelTestBody",
    "ModelsUpdateBody",
    "SessionBody",
    "SessionPatch",
    "SessionResponse",
    "StrictModel",
    "ToolExecuteBody",
    "ToolExecuteResponse",
    "ToolSearchBody",
    "TraceResponse",
]
