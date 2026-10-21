"""Execute tools for the tenant runtime."""

from __future__ import annotations
import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from ..memory.budget import TurnMemoryBudget, _MEMORY_READ_TOOLS


async def execute_tools(
    self,
    tool_calls: list[dict[str, Any]],
    allowed_write_tools: set[str],
    step: int,
    on_event: Callable[[dict[str, Any]], Awaitable[None]] | None,
    memory_budget: TurnMemoryBudget | None = None,
) -> list[dict[str, Any]]:
    async def run_one(call: dict[str, Any]) -> dict[str, Any]:
        decoded = self.tool_presentation.decode(call["name"], call["arguments"])
        if isinstance(decoded, str):
            record = {
                "step": step,
                "name": call["name"],
                "arguments": call["arguments"],
                "ok": False,
                "elapsed_ms": 0,
                "preview": decoded[:300],
                "content": decoded,
                "parallel": len(tool_calls) > 1,
            }
        else:
            tool_name, tool_args = decoded
            blocked = bool(memory_budget and not memory_budget.allow_call(tool_name))
            budget_blocked = blocked
            budget_truncated = False
            if blocked:
                content = "[本轮记忆检索调用预算已用尽；工具未执行]"
                tool_ok, elapsed_ms = False, 0
            else:
                tool_result = await self.tools.execute(
                    tool_name,
                    tool_args,
                    allowed_write_tools=allowed_write_tools,
                )
                content = tool_result.content
                if tool_name == "access_claims":
                    content = "[claim-access-atomic]\n" + content
                tool_ok, elapsed_ms = tool_result.ok, tool_result.elapsed_ms
                if memory_budget and tool_name in _MEMORY_READ_TOOLS:
                    consumed = memory_budget.consume(
                        content,
                        atomic=tool_name in {"access_claims", "search_library", "search_knowledge"},
                    )
                    if consumed != content:
                        if consumed.startswith("[本轮记忆检索预算已用尽"):
                            budget_blocked = True
                            tool_ok = False
                        else:
                            budget_truncated = True
                    content = consumed
            record = {
                "step": step,
                "name": tool_name,
                "arguments": tool_args,
                "ok": tool_ok,
                "elapsed_ms": elapsed_ms,
                "preview": content[:300],
                "content": content,
                "parallel": len(tool_calls) > 1,
            }
            if tool_ok:
                record["memory_dependencies"] = getattr(tool_result, "memory_dependencies", [])
            if budget_blocked:
                record["budget_blocked"] = True
            if budget_truncated:
                record["budget_truncated"] = True
            if call["name"] == "tool_call":
                record["via"] = "tool_call"
            if tool_name == "load_skill" and tool_ok and self.skills:
                try:
                    loaded = json.loads(content)
                except (TypeError, ValueError):
                    loaded = {}
                if isinstance(loaded, dict) and loaded.get("instructions") and loaded.get("name"):
                    record["loaded_skill"] = {
                        "name": loaded["name"],
                        "revision": loaded.get("revision", ""),
                    }
        if on_event:
            await on_event(
                {
                    "type": "tool",
                    "tool": {
                        key: value
                        for key, value in record.items()
                        if key not in {"content", "memory_dependencies"}
                    },
                }
            )
        return record

    if len(tool_calls) <= 1:
        return [await run_one(tool_calls[0])] if tool_calls else []
    return list(await asyncio.gather(*(run_one(call) for call in tool_calls)))
