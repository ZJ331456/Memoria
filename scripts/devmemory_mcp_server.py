"""DevMemory MCP stdio server: expose Memoria project memory to external coding agents.

Cursor / Claude Code 等编程 Agent 通过本 server 以 MCP 协议读写当前账号的项目记忆：
登录走正式账号（MEMORIA_USERNAME/MEMORIA_PASSWORD），全部业务经 HTTP API 进入
租户 Kernel，不直接访问数据库，也不绕过来源与反馈边界。

环境变量：
    MEMORIA_URL       默认 http://127.0.0.1:2237
    MEMORIA_USERNAME  必填
    MEMORIA_PASSWORD  必填
    MEMORIA_PROJECT   可选；设置后 tools/call 未指定 project 时使用它

工具：
    list_projects           列出项目工作区
    project_context         项目详情（来源、任务、最近事件）
    recall_project_memory   任务前项目内检索（含开放任务）
    record_task_result      任务后记录结果与验收反馈
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "memoria-devmemory", "version": "0.1.0"}


class MemoriaHttpBackend:
    """HTTP-only client for one logged-in Memoria account; no direct DB access."""

    def __init__(self, base_url: str, username: str, password: str):
        self.base = base_url.rstrip().rstrip("/")
        if self.base.endswith("/api"):
            self.base = self.base[: -len("/api")]
        if not self.base.startswith(("http://", "https://")):
            raise ValueError("MEMORIA_URL 必须是 http(s) 地址")
        self.username, self.password = username, password
        self.token: str | None = None

    def _request(self, method: str, path: str, payload: dict | None = None,
                 *, authenticated: bool = True, timeout: int = 60):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=body, method=method)
        request.add_header("Content-Type", "application/json")
        if authenticated and self.token:
            request.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", "replace")
                return response.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, raw

    def login(self) -> None:
        try:
            status, body = self._request(
                "POST", "/api/auth/login",
                {"username": self.username, "password": self.password}, authenticated=False,
            )
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"无法连接 Memoria 服务（{self.base}）：{getattr(exc, 'reason', exc)}"
            ) from exc
        if status != 200 or not isinstance(body, dict) or not body.get("access_token"):
            detail = body.get("message") if isinstance(body, dict) else body
            raise RuntimeError(f"Memoria 登录失败（{status}）：{detail}")
        self.token = body["access_token"]

    def call(self, method: str, path: str, payload: dict | None = None) -> dict:
        """One authenticated call; a single re-login covers expired sessions."""
        if self.token is None:
            self.login()
        status, body = self._request(method, path, payload)
        if status == 401:
            self.token = None
            self.login()
            status, body = self._request(method, path, payload)
        if status >= 400:
            message = body.get("message", body) if isinstance(body, dict) else body
            raise RuntimeError(f"Memoria API {status}：{message}")
        return body if isinstance(body, (dict, list)) else {}

    # ---- tool backends ----------------------------------------------------

    def _resolve(self, project: str) -> str:
        projects = self.call("GET", "/api/projects")
        default = str(getattr(self, "default_project", "") or "").strip()
        key = (project or "").strip() or default
        for item in projects:
            if item["id"] == key or item["name"] == key:
                return item["id"]
        available = ", ".join(item["name"] for item in projects) or "（暂无项目）"
        raise RuntimeError(f"项目不存在：{key or '（未指定）'}；当前项目：{available}")

    def list_projects(self, args: dict) -> dict:
        projects = self.call("GET", "/api/projects")
        return {"projects": [
            {"id": item["id"], "name": item["name"], "description": item["description"],
             "repo_path": item["repo_path"], "sources": item["sources"], "open_tasks": item["open_tasks"]}
            for item in projects
        ]}

    def project_context(self, args: dict) -> dict:
        pid = self._resolve(str(args.get("project", "")))
        detail = self.call("GET", f"/api/projects/{pid}")
        detail["sources"] = [
            {key: item.get(key) for key in ("kind", "title", "origin", "live_status", "outcome")}
            for item in detail.get("sources", [])
        ]
        return detail

    def recall_project_memory(self, args: dict) -> dict:
        pid = self._resolve(str(args.get("project", "")))
        return self.call("POST", f"/api/projects/{pid}/recall", {
            "query": str(args.get("query", "")),
            "limit": int(args.get("limit", 6)),
        })

    def record_task_result(self, args: dict) -> dict:
        pid = self._resolve(str(args.get("project", "")))
        task = self.call("POST", f"/api/projects/{pid}/tasks", {
            "title": str(args.get("title", "")), "detail": str(args.get("detail", "")),
        })
        return self.call("POST", f"/api/projects/{pid}/tasks/{task['id']}/finish", {
            "outcome": str(args.get("outcome", "unknown")),
            "content": str(args.get("content", "")),
        })


TOOL_SCHEMAS = {
    "list_projects": {
        "type": "object", "properties": {}, "additionalProperties": False,
    },
    "project_context": {
        "type": "object",
        "properties": {"project": {"type": "string", "minLength": 1, "maxLength": 80}},
        "required": ["project"], "additionalProperties": False,
    },
    "recall_project_memory": {
        "type": "object",
        "properties": {
            "project": {"type": "string", "minLength": 1, "maxLength": 80},
            "query": {"type": "string", "minLength": 1, "maxLength": 300},
            "limit": {"type": "integer", "minimum": 1, "maximum": 8},
        },
        "required": ["project", "query"], "additionalProperties": False,
    },
    "record_task_result": {
        "type": "object",
        "properties": {
            "project": {"type": "string", "minLength": 1, "maxLength": 80},
            "title": {"type": "string", "minLength": 1, "maxLength": 120},
            "content": {"type": "string", "minLength": 1, "maxLength": 3000},
            "outcome": {"type": "string", "enum": ["success", "failure", "unknown"]},
            "detail": {"type": "string", "maxLength": 4000},
        },
        "required": ["project", "title", "content"], "additionalProperties": False,
    },
}

TOOL_DESCRIPTIONS = {
    "list_projects": "列出当前账号的 Memoria 项目记忆工作区。",
    "project_context": "查看项目详情：已绑定来源（Git 历史、文档）、任务与最近事件。",
    "recall_project_memory":
        "任务开始前检索项目记忆：历史决策、失败尝试、任务结果与经验卡片；先召回再动手。",
    "record_task_result":
        "任务结束后记录结果与验收反馈；流程完成不代表成功，failure 同样值得记录。",
}

TOOLS_DECLARATION = [
    {"name": name, "description": TOOL_DESCRIPTIONS[name], "inputSchema": TOOL_SCHEMAS[name]}
    for name in TOOL_SCHEMAS
]


class DevMemoryServer:
    """Pure protocol handler: request dict in, response dict out (None for notifications)."""

    def __init__(self, backend):
        self.backend = backend

    def handle(self, message: dict) -> dict | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return self._error(None, -32600, "请求不是 JSON-RPC 2.0")
        if "id" not in message:
            return None  # notification (e.g. notifications/initialized): no reply
        method = message.get("method", "")
        if method == "initialize":
            return self._result(message["id"], {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            })
        if method == "ping":
            return self._result(message["id"], {})
        if method == "tools/list":
            return self._result(message["id"], {"tools": TOOLS_DECLARATION})
        if method == "tools/call":
            return self._tool_call(message)
        return self._error(message["id"], -32601, f"未知方法：{method}")

    def _tool_call(self, message: dict) -> dict:
        params = message.get("params") or {}
        name = str(params.get("name", ""))
        if name not in TOOL_SCHEMAS:
            return self._error(message["id"], -32602, f"未知工具：{name}")
        handler = getattr(self.backend, name, None)
        if handler is None:
            return self._result(message["id"], {
                "content": [{"type": "text", "text": f"DevMemory 调用失败：后端缺少 {name}"}],
                "isError": True,
            })
        try:
            result = handler(params.get("arguments") or {})
        except Exception as exc:  # tool errors are results, not protocol failures
            return self._result(message["id"], {
                "content": [{"type": "text", "text": f"DevMemory 调用失败：{exc}"}],
                "isError": True,
            })
        return self._result(message["id"], {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=1)}],
        })

    @staticmethod
    def _result(mid, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    @staticmethod
    def _error(mid, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def main() -> int:
    username, password = os.environ.get("MEMORIA_USERNAME", ""), os.environ.get("MEMORIA_PASSWORD", "")
    if not username or not password:
        print("缺少 MEMORIA_USERNAME / MEMORIA_PASSWORD 环境变量", file=sys.stderr)
        return 2
    backend = MemoriaHttpBackend(os.environ.get("MEMORIA_URL", "http://127.0.0.1:2237"), username, password)
    backend.default_project = os.environ.get("MEMORIA_PROJECT", "")  # reserved for tools
    server = DevMemoryServer(backend)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            response = DevMemoryServer._error(None, -32700, "无法解析的 JSON")
        else:
            response = server.handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
