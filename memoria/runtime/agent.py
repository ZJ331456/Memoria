"""Tenant turn lifecycle. Memory answers, dialogue preparation and tool loops have explicit boundaries."""

from __future__ import annotations
import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from ..config import Settings
from ..memory.access import infer_query
from ..memory.reader import SAFE_UNKNOWN
from ..lifecycle import Phase, Pipeline, TurnContext
from ..llm import LLMClient
from ..memory import MemoryQueryPlanner
from ..memory.budget import TurnMemoryBudget
from ..observability import EventBus, RequestContext, TurnTracer
from ..prompting import ContextBudget, PromptAssembler
from ..prompting.reader_budget import ReaderBudget
from ..skills import SkillCatalog
from ..store import Store
from ..tools import ToolPolicy, ToolPresentation, ToolRegistry
from ..tools.policy import ToolAuthorization
from .compaction import SessionCompactor
from .dialogue_context import assemble_dialogue
from .dialogue_reasoning import reason_dialogue
from .memory_turn import run_memory_turn
from .tool_execution import execute_tools

logger = logging.getLogger(__name__)


class AgentRuntime:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        llm: LLMClient,
        tools: ToolRegistry,
        *,
        kernel,
        pipeline: Pipeline | None = None,
        event_bus: EventBus | None = None,
        skills: SkillCatalog | None = None,
        tool_presentation: ToolPresentation | None = None,
    ):
        self.settings = settings
        self.store = store
        self.llm = llm
        if kernel is None:
            raise TypeError("A tenant-bound MemoryKernel is required")
        self.kernel = kernel
        self.memory = kernel.memory
        self.formation = kernel.formation
        self.library = kernel.library
        self.tools = tools
        self.skills = skills
        self.markdown = kernel.markdown
        self.tool_presentation = tool_presentation or ToolPresentation(tools, enabled=False)
        self.episodes = kernel.episodes
        self.claims = kernel.claims
        self.layers = getattr(settings, "memory_layers", None)
        self.pipeline = pipeline or Pipeline()
        self.event_bus = event_bus or EventBus()
        self.context_budget = ContextBudget(settings.context_char_budget)
        self.reader_budget = getattr(llm, "reader_budget", None) or ReaderBudget(settings)
        self.retrieval_planner = MemoryQueryPlanner(llm.plan_memory_retrieval)
        self.tool_policy = ToolPolicy()
        if self.layers and self.layers.enabled:
            self.prompt_assembler = PromptAssembler(
                section_limits={
                    "Long-term Memory": self.layers.semantic_chars,
                    "Past Task Episodes": self.layers.episodic_chars,
                    "Active Skills": self.layers.procedural_chars,
                    "Session Summary": self.layers.summary_chars,
                    "Self": self.layers.self_chars,
                    "Available Skills": self.layers.catalog_chars,
                    "Tool Search": self.layers.catalog_chars,
                },
                max_frame_chars=min(self.layers.context_chars, self.layers.retrieval_max_chars),
            )
        else:
            self.prompt_assembler = PromptAssembler()
        self.compactor = SessionCompactor(store, _FastSummarizer(llm))

    # Absolute ceiling even when max_iterations=0 (unlimited). Use /cancel to stop earlier.
    HARD_ITERATION_CAP = 200

    def _iteration_limit(self) -> int | None:
        value = int(self.settings.max_iterations)
        if value <= 0:
            return None
        return max(1, min(value, self.HARD_ITERATION_CAP))

    async def run(
        self,
        session_id: str,
        user_text: str,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        request_id: str | None = None,
        *,
        authorization: ToolAuthorization | None = None,
        iteration_limit: int | None = None,
        skip_memory_enqueue: bool = False,
        force_skill: str | None = None,
        turn_kind: str = "chat",
    ) -> tuple[dict, list[dict], dict]:
        turn_id = uuid.uuid4().hex
        tracer = TurnTracer(self.store, session_id)
        context = TurnContext(session_id, user_text)
        context.metadata["turn_id"] = turn_id
        context.metadata["turn_kind"] = turn_kind
        user_message: dict[str, Any] | None = None
        memory_dependencies = {}
        claim_access = claim_request = None
        memory_budget = (
            TurnMemoryBudget(self.layers.retrieval_max_calls, self.layers.retrieval_max_chars)
            if self.layers and self.layers.enabled
            else None
        )
        from ..observability.memory_trace import observe_reads

        with RequestContext(
            session_id=session_id, turn_id=turn_id, request_id=request_id
        ), observe_reads() as memory_reads:
            try:
                await self.pipeline.run(Phase.BEFORE_TURN, context)
                await self.event_bus.emit(
                    "turn.before", {"session_id": session_id, "turn_id": turn_id}
                )
                if on_event:
                    await on_event({"type": "phase", "phase": "before_turn"})

                kernel = self.kernel
                write_message = kernel.record_message
                user_message = write_message(session_id, "user", user_text)
                from ..provenance import current_ingress

                context.metadata["message_origin"] = (
                    current_ingress.get() or {"channel": "unknown"}
                ).get("channel")
                context.metadata["user_message_id"] = user_message["id"]
                session = self.store.session(session_id)
                if session and session["title"] == "新对话":
                    self.store.rename_session(session_id, user_text.replace("\n", " ")[:28])

                interrupt_note = (session or {}).get("interrupt_note") or ""
                claim_request = infer_query(user_text) if self.claims else None
                context.metadata["query_route"] = "personal_memory" if claim_request else "dialogue"
                if claim_request:
                    claim_request.session_id = session_id
                if claim_request:
                    return await self._memory_turn(
                        kernel,
                        claim_request,
                        context,
                        tracer,
                        user_message,
                        write_message,
                        on_event,
                        skip_memory_enqueue,
                        turn_kind,
                        turn_id,
                    )
                with tracer.timings.measure("compaction"):
                    compaction = (
                        None
                        if claim_request
                        else await self.compactor.ensure(session_id, self.settings.memory_window)
                    )
                if compaction:
                    context.metadata["compaction"] = {
                        "reused": compaction.reused,
                        "covered": len(compaction.covered_message_ids),
                        "summary_chars": len(compaction.summary),
                    }

                history = self.store.messages(session_id, self.settings.memory_window)
                claim_access = None
                with tracer.timings.measure("query_planner"):
                    plan = await self.retrieval_planner.plan(user_text, history[:-1])
                context.metadata["retrieval_plan"] = plan.public_dict()
                with tracer.timings.measure("memory.retrieve"):
                    context.memories = (
                        await kernel.recall(
                            plan.query,
                            plan.limit,
                            plan.kinds or None,
                            trace_query=user_text,
                            timings=tracer.timings,
                        )
                        if plan.needed
                        else []
                    )
                context.metadata["retrieval"] = {
                    "injected": len(context.memories),
                    "top_score": (
                        context.memories[0].get("retrieval", {}).get("score")
                        if context.memories
                        else None
                    ),
                    "sufficient": bool(
                        context.memories
                        and context.memories[0].get("retrieval", {}).get("score", 0) > 0
                    ),
                }
                memory_dependencies.update(
                    {
                        "memory:" + m["id"]: m["retrieval"]["dependency"]
                        for m in context.memories
                        if m.get("retrieval", {}).get("dependency")
                    }
                )

                assemble_dialogue(
                    self,
                    context,
                    tracer,
                    history=history,
                    compaction=compaction,
                    interrupt_note=interrupt_note,
                    turn_kind=turn_kind,
                    force_skill=force_skill,
                    memory_budget=memory_budget,
                    memory_dependencies=memory_dependencies,
                )

                step = await reason_dialogue(
                    self,
                    context,
                    tracer,
                    on_event=on_event,
                    iteration_limit=iteration_limit,
                    authorization=authorization,
                    memory_budget=memory_budget,
                    memory_dependencies=memory_dependencies,
                )

                await self.pipeline.run(Phase.AFTER_REASONING, context)
                with self.store.memory_transaction():
                    if any(
                        not self._memory_dependency_valid(d) for d in memory_dependencies.values()
                    ):
                        context.response = SAFE_UNKNOWN
                        context.metadata["memory_freshness"] = "dependency_changed_before_publish"
                    assistant_message = write_message(session_id, "assistant", context.response)
                if memory_dependencies and on_event:
                    tracer.mark_visible_text(context.response)
                    await on_event({"type": "delta", "content": context.response})
                self._audit_memory_path(
                    context, claim_access, claim_request, memory_reads, "dialogue"
                )
                if not skip_memory_enqueue:
                    memory_job = self.store.enqueue_memory_job(
                        user_message["id"], user_text, context.response
                    )
                    context.metadata["memory_job"] = {
                        "id": memory_job["id"],
                        "source_ref": user_message["id"],
                        "status_at_enqueue": memory_job["status"],
                    }
                created: list[dict] = []
                await self.pipeline.run(Phase.AFTER_TURN, context)
                await self.event_bus.emit(
                    "turn.after",
                    {
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "status": "completed",
                        "steps": step,
                    },
                )
                trace = tracer.finish(
                    "completed",
                    max(1, len(context.tool_chain) + 1),
                    context.memories,
                    context.tool_chain,
                    metadata=context.metadata,
                )
                self._record_episode_safe(
                    turn_kind,
                    session_id,
                    user_message,
                    assistant_message,
                    user_text,
                    "completed",
                    context.response,
                    "",
                    context.tool_chain,
                    trace["id"],
                )
                return assistant_message, created, trace
            except asyncio.CancelledError:
                self._audit_memory_path(
                    context, claim_access, claim_request, memory_reads, "cancelled"
                )
                note = f"上一轮在处理「{user_text[:80]}」时被中断；已完成 {len(context.tool_chain)} 个工具步骤。"
                self.store.set_interrupt_note(session_id, note)
                await self.event_bus.emit(
                    "turn.cancelled", {"session_id": session_id, "turn_id": turn_id}
                )
                trace = tracer.finish(
                    "cancelled",
                    len(context.tool_chain),
                    context.memories,
                    context.tool_chain,
                    "turn cancelled",
                    context.metadata,
                )
                self._record_episode_safe(
                    turn_kind,
                    session_id,
                    user_message,
                    None,
                    user_text,
                    "cancelled",
                    "",
                    "turn cancelled",
                    context.tool_chain,
                    trace["id"],
                )
                raise
            except Exception as exc:
                self._audit_memory_path(
                    context, claim_access, claim_request, memory_reads, "execution_failure"
                )
                await self.event_bus.emit(
                    "turn.failed",
                    {
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                trace = tracer.finish(
                    "failed",
                    len(context.tool_chain),
                    context.memories,
                    context.tool_chain,
                    f"{type(exc).__name__}: {exc}",
                    context.metadata,
                )
                self._record_episode_safe(
                    turn_kind,
                    session_id,
                    user_message,
                    None,
                    user_text,
                    "failed",
                    "",
                    f"{type(exc).__name__}: {exc}",
                    context.tool_chain,
                    trace["id"],
                )
                raise

    async def _memory_turn(
        self,
        kernel,
        request,
        context,
        tracer,
        user_message,
        write_message,
        on_event,
        skip_memory_enqueue,
        turn_kind,
        turn_id,
    ):
        return await run_memory_turn(
            self,
            kernel,
            request,
            context,
            tracer,
            user_message,
            write_message,
            on_event,
            skip_memory_enqueue,
            turn_kind,
            turn_id,
        )

    def _memory_dependency_valid(self, dependency: dict) -> bool:
        from ..observability.context import current_session_id

        return self.kernel.dependency_valid(dependency, session_id=current_session_id.get())

    async def _execute_tools_parallel(
        self, tool_calls, allowed_write_tools, step, on_event, memory_budget=None
    ):
        return await execute_tools(
            self, tool_calls, allowed_write_tools, step, on_event, memory_budget
        )

    def _record_episode_safe(
        self,
        turn_kind: str,
        session_id: str,
        user_message: dict[str, Any] | None,
        assistant_message: dict[str, Any] | None,
        task: str,
        outcome: str,
        result: str,
        error: str,
        tool_chain: list[dict[str, Any]],
        trace_id: str,
    ) -> None:
        if turn_kind != "chat" or not self.episodes or not user_message:
            return
        try:
            self.episodes.record(
                session_id=session_id,
                user_message_id=user_message["id"],
                assistant_message_id=assistant_message["id"] if assistant_message else None,
                task=task[:600],
                outcome=outcome,
                result=result[:1200],
                error=error[:300],
                tool_names=[
                    item["name"] for item in tool_chain if self.tools.get(item.get("name", ""))
                ],
                trace_id=trace_id,
            )
        except Exception:
            logger.exception("failed to record task episode for trace %s", trace_id)

    async def _reader_chat(self, context, messages, *, stream=False, stage="reader", **kwargs):
        import time
        from ..observability.memory_trace import fingerprint, read_visibility

        attempt = {
            "stage": stage,
            "status": "started",
            "usage": None,
            "retries": 0,
            "request_sha256": fingerprint(messages),
            "memory_read_visibility": read_visibility(messages),
            "budget": context.metadata.get("reader_budget"),
            "scope": "LLM client request attempt; provider/network acceptance unknown on failure",
        }
        context.metadata.setdefault("llm_calls", []).append(attempt)
        from ..llm.deepseek_cache import official
        attempt['cache_provider']='deepseek' if official(kwargs.get('model') or self.settings.main) else None
        started = time.perf_counter()
        try:
            result = await (
                self.llm.chat_stream(messages, **kwargs)
                if stream
                else self.llm.chat(messages, **kwargs)
            )
            attempt.update(
                status="returned",
                usage=result.usage or None,
                retries=result.retries,
                finish_reason=result.finish_reason,
            )
            return result
        except BaseException as exc:
            attempt.update(status="failed", error_type=type(exc).__name__)
            raise
        finally:
            attempt["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)

    def _audit_memory_path(self, context, access, request, reads, status):
        kernel = self.kernel
        from ..observability.memory_trace import answer_trace

        if access is None:
            access = {
                "path": "dialogue",
                "candidates": [c for r in reads for c in r["candidates"]],
                "selection": {"payload": {"evidence": [c for r in reads for c in r["selected"]]}},
            }
        elif request:
            access = kernel.guard_pending_revisions(dict(access), request)
        try:
            trace = answer_trace(
                kernel,
                request.model_dump(mode="json") if request else {"query": context.user_text},
                {
                    "access": access,
                    "reader_called": bool(context.metadata.get("llm_calls")),
                    "status": status,
                    "answer": context.response,
                    "reader_budget": context.metadata.get("reader_budget"),
                    "validation": context.metadata.get(
                        "citation_validation",
                        {"valid": None, "reason": "dialogue_has_no_strict_citation_contract"},
                    ),
                    "citations": context.metadata.get("citations", []),
                },
                calls=context.metadata.get("llm_calls", []),
            )
            if not request:
                trace["selection_scope"] = (
                    "retrieval/tool selection; actual request fragment visibility is in reader.calls"
                )
                from ..observability.memory_trace import fingerprint

                trace["trace_sha256"] = fingerprint(
                    {k: v for k, v in trace.items() if k != "trace_sha256"}
                )
            context.metadata["memory_closed_loop"] = trace
        except Exception as exc:
            context.metadata["memory_closed_loop_error"] = type(exc).__name__


class _FastSummarizer:
    """Prefer the cheap fast model for session compaction summaries."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def complete(
        self, messages: list[dict[str, str]], model=None, max_tokens: int | None = None
    ) -> str:
        selected = model
        if selected is None:
            selected = self.llm.settings.preferred_chat_model(prefer_fast=True)
        return await self.llm.complete(messages, model=selected, max_tokens=max_tokens)
