from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from collections.abc import Awaitable, Callable
from typing import Any

from .config import Settings
from .drift import DriftWorker
from .llm import LLMClient
from .store import Store
from .memory.kernel import MemoryKernel
from .memory.forgetting import ForgettingWorker
from .mcp import McpHost
from .models_config import public_models, save_model_overrides, test_model_slot
from .memory.embedding import EmbeddingError
from .runtime import AgentRuntime
from .skills import SkillCatalog
from .tools import ToolPresentation, build_registry

logger = logging.getLogger(__name__)


class AgentService:
    def __init__(self, settings: Settings, store: Store, llm: LLMClient, *, user_id: str = "local"):
        self.settings, self.store, self.llm = settings, store, llm
        self.kernel = MemoryKernel(settings, store, llm, user_id=user_id)
        self.skills = (
            SkillCatalog(settings.skills_directory, max_inject=settings.skills_max_inject)
            if settings.skills_enabled
            else None
        )
        layers = getattr(settings, "memory_layers", None)
        self.forgetting_worker = (
            ForgettingWorker(self.episodes, layers.maintenance_interval_seconds)
            if self.episodes else None
        )
        tools = build_registry(
            self.kernel, self.skills,
            http_allowed_hosts=settings.http_allowed_hosts,
        )
        from .tools.knowledge import register_knowledge_tools
        register_knowledge_tools(tools, self.kernel)
        self.tool_presentation = ToolPresentation(tools, enabled=settings.tool_search_enabled)
        from .mcp.config import configured_mcp_servers
        self.mcp = McpHost(tools, settings.mcp_config_file, enabled=settings.mcp_enabled,
                           loader=lambda: configured_mcp_servers(settings))
        self.runtime = AgentRuntime(
            settings,
            store,
            llm,
            tools,
            kernel=self.kernel,
            skills=self.skills,
            tool_presentation=self.tool_presentation,
        )
        self._busy_check: Callable[[], bool] = lambda: False
        self._integration_task: asyncio.Task | None = None
        self._integration_state = "not_started"
        self._integration_error = ""
        self.drift = DriftWorker(
            store=store,
            runtime=self.runtime,
            skills=self.skills,
            markdown=self.markdown,
            settings=settings,
            is_busy=lambda: self._busy_check(),
        )

    @property
    def claims(self):
        return self.kernel.claims

    @property
    def markdown(self):
        return self.kernel.markdown

    @property
    def embedder(self):
        return self.kernel.embedder

    @property
    def episodes(self):
        return self.kernel.episodes

    @property
    def auto_review(self):
        return self.kernel.auto_review

    @property
    def formation(self):
        return self.kernel.formation

    @property
    def library(self):
        return self.kernel.library

    @property
    def memory_worker(self):
        return self.kernel.memory_worker

    def set_busy_check(self, check: Callable[[], bool]) -> None:
        self._busy_check = check

    def start_background_integrations(self) -> None:
        """Optional model warmup/MCP connections must not gate tenant data reads."""
        if self._integration_task is not None:
            return
        self._integration_state = "starting"

        async def initialize():
            try:
                await self.start_integrations()
                unavailable = any(s["enabled"] and not s["connected"] for s in self.mcp.status()["servers"])
                self._integration_state = "degraded" if unavailable else "ready"
            except asyncio.CancelledError:
                self._integration_state = "stopped"
                raise
            except Exception as exc:
                # Provider errors can contain credentials; expose only the class.
                self._integration_state = "failed"
                self._integration_error = type(exc).__name__
                logger.warning("账号集成初始化失败 type=%s", type(exc).__name__)

        self._integration_task = asyncio.create_task(initialize(), name="memoria-integrations")

    def integration_status(self) -> dict[str, str]:
        return {"state": self._integration_state, "error": self._integration_error}

    async def reload_mcp(self) -> dict[str, Any]:
        # Avoid racing startup registration/cleanup with an explicit reload.
        if self._integration_task is not None:
            await asyncio.shield(self._integration_task)
        servers = await self.mcp.reload()
        return {"servers": [item.public_dict() for item in servers], "status": self.mcp.status()}

    async def start_integrations(self) -> None:
        if self.embedder.is_local and self.embedder.enabled:
            logger.info("正在预热本地 Embedding，首次启动需要加载模型")
        try:
            await self.embedder.warmup()
        except EmbeddingError as exc:
            # Keep setup available so the user can repair a missing model/device.
            logger.warning("本地 Embedding 启动预热失败：%s", exc)
        else:
            if self.embedder.is_local and self.embedder.enabled:
                logger.info("本地 Embedding 预热完成")
        if self.settings.main.is_ready(role="llm"):
            from .llm import ProviderError
            from .llm.backends.resolve import backend_public_label

            label = backend_public_label(self.settings.main)
            if self.settings.main.is_local_process():
                logger.info("正在预热进程内本地 LLM（%s）", label)
                try:
                    await self.llm.chat([{"role": "user", "content": "ping"}], max_tokens=8)
                except (ProviderError, RuntimeError, OSError, ImportError) as exc:
                    logger.warning("本地 LLM 启动预热失败：%s", exc)
                else:
                    logger.info("本地 LLM 预热完成")
            elif self.settings.main.is_local_http():
                logger.info(
                    "主模型使用 OpenAI 兼容 HTTP（%s → %s）；请确保已启动 scripts/serve_local_llm.py 或 vLLM/SGLang/Ollama",
                    label,
                    self.settings.main.base_url,
                )
        await self.mcp.start()

    async def stop_integrations(self) -> None:
        if self._integration_task is not None:
            self._integration_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._integration_task
            self._integration_task = None
        self._integration_state = "stopped"
        await self.mcp.stop()

    async def chat(self, session_id: str, content: str) -> tuple[dict, list[dict]]:
        message, memories, _ = await self.chat_with_trace(session_id, content)
        return message, memories

    async def chat_with_trace(
        self,
        session_id: str,
        content: str,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        request_id: str | None = None,
    ) -> tuple[dict, list[dict], dict]:
        if not self.store.session(session_id):
            raise KeyError(session_id)
        return await self.runtime.run(session_id, content, on_event, request_id=request_id)

    def update_models(self, updates: dict[str, dict[str, str]]) -> dict[str, Any]:
        result = save_model_overrides(self.settings, updates)
        # In-flight embedding requests keep their original client/model identity.
        self.kernel.configure_embedding()
        return result

    def models_public(self) -> dict[str, Any]:
        return public_models(self.settings)

    async def test_model(self, slot: str) -> dict[str, Any]:
        return await test_model_slot(self.settings, slot)
