from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


Executor = Callable[[dict[str, Any]], Awaitable[Any]]
Hook = Callable[["Tool", dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    executor: Executor
    risk: str = "read-only"
    timeout_seconds: float | None = None
    max_output_chars: int = 12000
    owner: str = "builtin"
    search_hint: str = ""
    always_on: bool = True

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


@dataclass(slots=True)
class ToolValue:
    """Trusted server-side evidence metadata, independent of serialized output."""
    value: Any
    memory_dependencies: list[dict] = field(default_factory=list)


@dataclass(slots=True)
class ToolResult:
    name: str
    ok: bool
    content: str
    elapsed_ms: int
    memory_dependencies: list[dict] = field(default_factory=list)


class ToolRegistry:
    def __init__(self, timeout: float = 15.0):
        self._tools: dict[str, Tool] = {}
        self._hooks: list[Hook] = []
        self.timeout = timeout

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools: raise ValueError(f"工具重复注册: {tool.name}")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        return self._tools.pop(name, None) is not None

    def unregister_owner(self, owner: str) -> list[str]:
        removed = [name for name, tool in self._tools.items() if tool.owner == owner]
        for name in removed:
            del self._tools[name]
        return removed

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def register_hook(self, hook: Hook) -> None:
        self._hooks.append(hook)

    def schemas(self) -> list[dict[str, Any]]:
        # Always-on tools first, then the rest, each group sorted. Registration order depends
        # on import/plugin timing and per-turn tool search, so an unsorted list silently
        # changes the prompt prefix and cold-starts the cache behind it.
        order = sorted(self._tools.values(), key=lambda t: (not t.always_on, t.name))
        return [tool.schema() for tool in order]

    def catalog(self) -> list[dict[str, Any]]:
        order = sorted(self._tools.values(), key=lambda t: (not t.always_on, t.name))
        return [
            {
                "name": t.name,
                "description": t.description,
                "risk": t.risk,
                "owner": t.owner,
                "always_on": t.always_on,
                "search_hint": t.search_hint,
            }
            for t in order
        ]

    async def execute(self, name: str, arguments: dict[str, Any], allow_write: bool = False, allowed_write_tools: set[str] | None = None) -> ToolResult:
        import time
        started = time.perf_counter()
        tool = self._tools.get(name)
        if not tool: return ToolResult(name, False, f"未知工具: {name}", 0)
        try:
            if tool.risk != "read-only" and not (allow_write or (allowed_write_tools and name in allowed_write_tools)):
                raise PermissionError("该工具会修改状态，当前 turn 未获得明确写入授权")
            self._validate(tool.parameters, arguments)
            for hook in self._hooks:
                await asyncio.wait_for(hook(tool, arguments), timeout=min(tool.timeout_seconds or self.timeout, 5.0))
            value = await asyncio.wait_for(tool.executor(arguments), timeout=tool.timeout_seconds or self.timeout)
            dependencies=[]
            if tool.owner == 'builtin' and isinstance(value, ToolValue):
                dependencies = value.memory_dependencies
                value = value.value
            # Capture trusted builtin dependencies before JSON/output budget truncation.
            if tool.owner=='builtin' and name in {'recall_memory','search_history'} and isinstance(value,list):
                for item in value:
                    if isinstance(item,dict):
                        dependency=(item.get('retrieval',{}).get('dependency') if name=='recall_memory' else item.get('source_dependency'))
                        if dependency:
                            dependencies.append(dependency)
            content = json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list)) else str(value)
            if len(content) > tool.max_output_chars:
                content = content[:tool.max_output_chars] + f"\n…[输出已截断，原始 {len(content)} 字符]"
            return ToolResult(name, True, content, int((time.perf_counter()-started)*1000),dependencies)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return ToolResult(name, False, f"{type(exc).__name__}: {exc}", int((time.perf_counter()-started)*1000))

    @staticmethod
    def _validate(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
        ToolRegistry._validate_value(schema, arguments, "arguments")

    @staticmethod
    def _validate_value(schema: dict[str, Any], value: Any, path: str) -> None:
        expected = schema.get("type")
        types = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
        if expected in {"integer", "number"} and isinstance(value, bool):
            raise ValueError(f"{path} 应为 {expected}")
        if expected in types and not isinstance(value, types[expected]):
            raise ValueError(f"{path} 应为 {expected}")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError(f"{path} 不在允许值中")
        if isinstance(value, str):
            if len(value) < int(schema.get("minLength", 0)): raise ValueError(f"{path} 过短")
            if "maxLength" in schema and len(value) > int(schema["maxLength"]): raise ValueError(f"{path} 过长")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]: raise ValueError(f"{path} 小于最小值")
            if "maximum" in schema and value > schema["maximum"]: raise ValueError(f"{path} 大于最大值")
        if isinstance(value, list):
            if "maxItems" in schema and len(value) > int(schema["maxItems"]): raise ValueError(f"{path} 项目过多")
            if schema.get("items"):
                for index, item in enumerate(value): ToolRegistry._validate_value(schema["items"], item, f"{path}[{index}]")
        if not isinstance(value, dict):
            return
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(props))
            if unknown: raise ValueError(f"未知参数: {', '.join(unknown)}")
        for field in schema.get("required", []):
            if field not in value: raise ValueError(f"缺少必填参数: {field}")
        for name, child in value.items():
            if name in props: ToolRegistry._validate_value(props[name], child, f"{path}.{name}")
