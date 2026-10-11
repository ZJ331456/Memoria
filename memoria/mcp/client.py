"""精简版 stdio MCP 客户端：initialize → tools/list → tools/call。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = "2024-11-05"
_SUPPORTED = frozenset({"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"})
_CONNECT_TIMEOUT = 45.0
_DEFAULT_CALL_TIMEOUT = 30.0
_STREAM_LIMIT = 4 * 1024 * 1024


@dataclass(slots=True)
class McpToolInfo:
    name: str
    description: str
    input_schema: dict[str, Any]


class McpToolExecutionError(RuntimeError):
    """远端工具执行失败。"""


class McpClient:
    def __init__(
        self,
        name: str,
        command: tuple[str, ...],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ):
        if not command:
            raise ValueError("MCP command 不能为空")
        self.name = name
        self.command = tuple(command)
        self.cwd = cwd
        self.env = dict(env or {})
        self._process: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._lock = asyncio.Lock()
        self._tool_infos: list[McpToolInfo] = []
        self._stderr_task: asyncio.Task[None] | None = None
        self._protocol_version: str | None = None

    @property
    def tool_infos(self) -> list[McpToolInfo]:
        return list(self._tool_infos)

    @property
    def connected(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def connect(self) -> list[McpToolInfo]:
        async with self._lock:
            if self.connected:
                return self._tool_infos
            try:
                return await asyncio.wait_for(self._connect_impl(), timeout=_CONNECT_TIMEOUT)
            except BaseException:
                await self._force_cleanup()
                raise

    async def _connect_impl(self) -> list[McpToolInfo]:
        merged = os.environ.copy()
        merged.update(self.env)
        logger.info("[mcp] 启动 %s", self.name)
        self._process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env=merged,
            limit=_STREAM_LIMIT,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr(), name=f"mcp-stderr:{self.name}")
        init = await self._request(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "memoria-agent", "version": "0.8.0"},
            },
        )
        version = init.get("protocolVersion", _PROTOCOL_VERSION)
        if version not in _SUPPORTED:
            raise RuntimeError(f"MCP server {self.name!r} 协议版本不受支持: {version!r}")
        self._protocol_version = str(version)
        await self._notify("notifications/initialized")
        raw_tools: list = []
        cursors: set[str] = set()
        params: dict[str, Any] = {}
        for _ in range(100):
            listed = await self._request("tools/list", params)
            page = listed.get("tools", [])
            if not isinstance(page, list):
                raise RuntimeError(f"MCP server {self.name!r} tools/list.tools 不是数组")
            raw_tools.extend(page)
            cursor = listed.get("nextCursor")
            if cursor is None:
                break
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise RuntimeError("MCP tools/list 返回无效或循环 cursor")
            cursors.add(cursor)
            params = {"cursor": cursor}
        else:
            raise RuntimeError("MCP tools/list 分页超过上限")
        tools: list[McpToolInfo] = []
        seen: set[str] = set()
        for item in raw_tools:
            if not isinstance(item, dict):
                raise RuntimeError(f"MCP server {self.name!r} 返回了无效 tool")
            tool_name = item.get("name")
            schema = item.get("inputSchema")
            description = item.get("description", "")
            if not isinstance(tool_name, str) or not tool_name or not isinstance(schema, dict):
                raise RuntimeError(f"MCP server {self.name!r} tool 缺少 name/inputSchema")
            if schema.get("type") != "object":
                raise RuntimeError(f"MCP server {self.name!r} tool {tool_name} inputSchema.type 必须是 object")
            if tool_name in seen:
                raise RuntimeError(f"MCP server {self.name!r} 返回重复工具: {tool_name}")
            seen.add(tool_name)
            tools.append(
                McpToolInfo(
                    name=tool_name,
                    description=description if isinstance(description, str) else "",
                    input_schema=dict(schema),
                )
            )
        self._tool_infos = tools
        logger.info("[mcp] %s 已连接，工具: %s", self.name, [tool.name for tool in tools])
        return tools

    async def call(self, tool_name: str, arguments: dict[str, Any], *, timeout: float | None = None) -> str:
        async with self._lock:
            if not self.connected:
                raise RuntimeError(f"MCP server {self.name!r} 未连接")
            result = await self._request(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
                timeout=timeout or _DEFAULT_CALL_TIMEOUT,
            )
        if result.get("isError"):
            raise McpToolExecutionError(self._render_content(result) or "远端工具返回 isError")
        return self._render_content(result)

    async def disconnect(self) -> None:
        async with self._lock:
            await self._force_cleanup()

    async def _force_cleanup(self) -> None:
        process = self._process
        self._process = None
        self._tool_infos = []
        self._protocol_version = None
        if process is None:
            return
        try:
            if process.stdin and not process.stdin.is_closing():
                process.stdin.close()
        except Exception:
            pass
        try:
            if process.returncode is None:
                if os.name == "nt":
                    # A Python launcher owns the actual server child. Terminate
                    # the entire owned tree so cancelled calls leave no server.
                    await asyncio.to_thread(subprocess.run,
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
                else:
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3.0)
                except TimeoutError:
                    process.kill()
                    await process.wait()
        except ProcessLookupError:
            pass
        stderr_task = self._stderr_task
        self._stderr_task = None
        if stderr_task is not None:
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)

    async def _request(self, method: str, params: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        request_id = self._new_id()
        await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        try:
            response = await self._recv(expected_id=request_id, timeout=timeout)
        except (TimeoutError, asyncio.CancelledError):
            # A timed-out process must not keep executing unseen side effects.
            await self._force_cleanup()
            raise
        if "error" in response:
            error = response["error"]
            detail = error.get("message", error) if isinstance(error, dict) else error
            raise RuntimeError(f"MCP {self.name}.{method} 错误: {detail}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"MCP {self.name}.{method} 缺少 result 对象")
        return result

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        await self._send(payload)

    async def _send(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise RuntimeError(f"MCP server {self.name!r} stdin 不可用")
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        process.stdin.write(data)
        await process.stdin.drain()

    async def _recv(self, *, expected_id: int, timeout: float | None = None) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdout is None:
            raise RuntimeError(f"MCP server {self.name!r} stdout 不可用")
        deadline = asyncio.get_running_loop().time() + (timeout or _DEFAULT_CALL_TIMEOUT)
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"MCP server {self.name!r} 等待响应超时")
            line = await asyncio.wait_for(process.stdout.readline(), timeout=remaining)
            if not line:
                raise RuntimeError(f"MCP server {self.name!r} 进程已退出")
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                logger.warning("[mcp] %s 忽略非 JSON 行", self.name)
                continue
            if not isinstance(message, dict):
                continue
            if "method" in message and "id" in message:
                reply: dict[str, Any] = {"jsonrpc": "2.0", "id": message["id"]}
                if message["method"] == "ping":
                    reply["result"] = {}
                else:
                    reply["error"] = {"code": -32601, "message": "Client capability not supported"}
                await self._send(reply)
                continue
            if message.get("id") == expected_id:
                return message
            # 忽略通知或其它 id；常见于 server 主动 log/progress。
            if "method" in message and "id" not in message:
                continue

    async def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    # Third-party logs can contain OAuth URLs or tokens.
                    logger.debug("[mcp:%s:stderr] received %s bytes", self.name, len(line))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[mcp] %s stderr 读取失败", self.name)

    def _new_id(self) -> int:
        value = self._next_id
        self._next_id += 1
        return value

    @staticmethod
    def _render_content(result: dict[str, Any]) -> str:
        content = result.get("content")
        if not isinstance(content, list):
            return json.dumps(result, ensure_ascii=False)
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
            else:
                parts.append(json.dumps(block, ensure_ascii=False))
        return "\n".join(parts)
