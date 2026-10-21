"""Tool-capable dialogue reasoning; memory facts are published by Kernel.answer."""

from __future__ import annotations
from ..memory.reader import SAFE_UNKNOWN
from ..lifecycle import Phase
from ..llm import ContextLengthError
from ..tools.loop_guard import ToolLoopGuard


async def reason_dialogue(
    runtime,
    context,
    tracer,
    *,
    on_event,
    iteration_limit,
    authorization,
    memory_budget,
    memory_dependencies,
):
    self = runtime
    session_id, user_text = context.session_id, context.user_text
    turn_id = context.metadata["turn_id"]
    await self.pipeline.run(Phase.BEFORE_REASONING, context)
    await self.event_bus.emit(
        "turn.before_reasoning", {"session_id": session_id, "turn_id": turn_id}
    )

    if iteration_limit is not None:
        max_steps = max(1, min(int(iteration_limit), self.HARD_ITERATION_CAP))
    else:
        max_steps = self._iteration_limit()
    loop_guard = ToolLoopGuard(max_identical=2)
    if authorization is None:
        authorization = self.tool_policy.authorize(user_text)
    context.metadata["tool_authorization"] = {
        "allowed_write_tools": sorted(authorization.allowed_write_tools),
        "reason": authorization.reason,
    }

    step = 0
    while True:
        ceiling = max_steps if max_steps is not None else self.HARD_ITERATION_CAP
        if step >= ceiling:
            context.response = "工具调用达到安全上限，请缩小任务范围后重试。"
            break
        step += 1
        with tracer.timings.measure("prompt.budget"):
            budget = self.context_budget.apply(context.messages)
        context.messages = budget.messages
        context.metadata["context_budget"] = {
            "original_chars": budget.original_chars,
            "final_chars": budget.final_chars,
            "dropped_messages": budget.dropped_messages,
            "truncated_tool_results": budget.truncated_tool_results,
            "truncated_messages": budget.truncated_messages,
            "preserved_context_frame": budget.preserved_context_frame,
        }
        if any(not self._memory_dependency_valid(d) for d in memory_dependencies.values()):
            context.response = SAFE_UNKNOWN
            context.metadata["memory_freshness"] = "dependency_changed_before_reader"
            break
        try:
            prepared = await self.reader_budget.prepare(
                context.messages,
                self.tool_presentation.schemas(),
                stream=bool(on_event and not memory_dependencies),
            )
        except ContextLengthError:
            context.response = "当前完整请求超过阅读预算，请缩短输入或减少上下文后重试。"
            context.metadata["reader_budget"] = {"fits": False, "request_sent": False}
            break
        context.messages = prepared.messages
        context.metadata["reader_budget"] = prepared.report
        try:
            tracer.mark_llm_request()
            if on_event and not memory_dependencies:

                async def stream_delta(text: str):
                    tracer.mark_visible_text(text)
                    await on_event({"type": "delta", "content": text})

                with tracer.timings.measure("llm.reader"):
                    result = await self._reader_chat(
                        context,
                        context.messages,
                        tools=prepared.tools,
                        on_delta=stream_delta,
                        stream=True,
                        model=prepared.model,
                        max_tokens=prepared.max_tokens,
                    )
            else:
                with tracer.timings.measure("llm.reader"):
                    result = await self._reader_chat(
                        context,
                        context.messages,
                        tools=prepared.tools,
                        model=prepared.model,
                        max_tokens=prepared.max_tokens,
                    )
        except ContextLengthError:
            emergency = self.context_budget.emergency(context.messages)
            context.messages = emergency.messages
            normal_stats = dict(context.metadata["context_budget"])
            context.metadata["context_budget"] = {
                "original_chars": normal_stats["original_chars"],
                "final_chars": emergency.final_chars,
                "dropped_messages": normal_stats["dropped_messages"] + emergency.dropped_messages,
                "truncated_tool_results": normal_stats["truncated_tool_results"]
                + emergency.truncated_tool_results,
                "truncated_messages": normal_stats["truncated_messages"]
                + emergency.truncated_messages,
                "preserved_context_frame": emergency.preserved_context_frame,
                "emergency_retry": True,
                "normal": normal_stats,
                "emergency": {
                    "original_chars": emergency.original_chars,
                    "final_chars": emergency.final_chars,
                    "dropped_messages": emergency.dropped_messages,
                    "truncated_tool_results": emergency.truncated_tool_results,
                    "truncated_messages": emergency.truncated_messages,
                    "preserved_context_frame": emergency.preserved_context_frame,
                },
            }
            prepared = await self.reader_budget.prepare(
                context.messages,
                prepared.tools,
                selected=prepared.model,
                max_tokens=prepared.max_tokens,
            )
            context.messages = prepared.messages
            context.metadata["reader_budget"] = prepared.report
            with tracer.timings.measure("llm.reader_emergency"):
                result = await self._reader_chat(
                    context,
                    context.messages,
                    tools=prepared.tools,
                    stage="reader_emergency",
                    model=prepared.model,
                    max_tokens=prepared.max_tokens,
                )
        if not result.tool_calls:
            context.response = result.content or "我暂时没有生成有效回复，请再试一次。"
            break

        assistant = {
            "role": "assistant",
            "content": result.raw_message.get("content"),
            "tool_calls": result.raw_message.get("tool_calls", []),
        }
        context.messages.append(assistant)
        allowed, signature = loop_guard.check(result.tool_calls)
        if not allowed:
            for call in result.tool_calls:
                context.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": "工具循环保护：相同调用已连续执行两次，本次不再执行。请基于已有结果给出阶段性结论。",
                    }
                )
            context.tool_chain.append(
                {
                    "step": step,
                    "name": "tool_loop_guard",
                    "arguments": {},
                    "ok": False,
                    "elapsed_ms": 0,
                    "preview": f"blocked repeated signature {signature[:160]}",
                }
            )
            final_messages = context.messages + [
                {
                    "role": "user",
                    "content": f"{user_text}\n\n请停止调用工具，基于已有结果直接给出简洁的最终答复；说明已经完成什么和仍缺少什么。",
                }
            ]
            final_budget = self.context_budget.apply(final_messages)
            context.metadata["context_budget"]["loop_guard_final_chars"] = final_budget.final_chars
            final_prepared = await self.reader_budget.prepare(final_budget.messages)
            context.metadata["reader_budget"] = final_prepared.report
            with tracer.timings.measure("llm.reader_loop_guard"):
                summary = await self._reader_chat(
                    context,
                    final_prepared.messages,
                    model=final_prepared.model,
                    max_tokens=final_prepared.max_tokens,
                    stage="reader_loop_guard",
                )
            context.response = summary.content or "检测到重复工具调用，已安全停止。"
            break

        with tracer.timings.measure("tools.execute"):
            tool_records = await self._execute_tools_parallel(
                result.tool_calls,
                authorization.allowed_write_tools,
                step,
                on_event,
                memory_budget,
            )
        context.tool_chain.extend(tool_records)
        for record in tool_records:
            for dependency in record.get("memory_dependencies", []):
                memory_dependencies[
                    dependency.get("dependency_type", "memory") + ":" + dependency["id"]
                ] = dependency
        if self.skills:
            context.metadata["skills"]["loaded"].extend(
                record["loaded_skill"] for record in tool_records if "loaded_skill" in record
            )
        if memory_budget:
            context.metadata["memory_tool_budget"] = memory_budget.public_dict()
        for call, record in zip(result.tool_calls, tool_records):
            context.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": record["content"],
                }
            )
        await self.pipeline.run(Phase.AFTER_STEP, context)
        await self.event_bus.emit(
            "turn.after_step",
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "step": step,
                "tools": len(tool_records),
            },
        )

    return step
