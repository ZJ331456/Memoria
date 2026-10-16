from __future__ import annotations

import json
import os
import re
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve_env(value: str) -> str:
    return _ENV.sub(lambda match: os.getenv(match.group(1), ""), value)


@dataclass(slots=True)
class McpServerConfig:
    name: str
    command: tuple[str, ...]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    allowed_tools: tuple[str, ...] = ()
    risk: str = "write"
    timeout_seconds: float = 30.0

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": list(self.command),
            "cwd": self.cwd,
            "enabled": self.enabled,
            "allowed_tools": list(self.allowed_tools),
            "risk": self.risk,
            "timeout_seconds": self.timeout_seconds,
            "env_keys": sorted(self.env),
        }


def load_mcp_servers(path: Path) -> list[McpServerConfig]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return parse_mcp_servers(raw)


def configured_mcp_servers(settings) -> list[McpServerConfig]:
    """One MCP document, explicitly enabled for one authenticated UUID only."""
    if not settings.mcp_enabled or settings.account_directory is None:
        return []
    from ..config import _merge
    from ..config_store import read_document
    document = read_document(settings.mcp_config_file) if settings.mcp_config_file.is_file() else {}
    account = document.get('accounts', {}).get(settings.account_directory.name, {}).get('mcp', {})
    enabled = account.get('enabled_servers', [])
    definitions = _merge(document.get('servers', {}), account.get('servers', {}))
    if not isinstance(enabled, list) or any(not isinstance(name, str) for name in enabled) or len(set(enabled)) != len(enabled):
        raise ValueError('accounts.<UUID>.mcp.enabled_servers must contain unique server names')
    if any(name not in definitions for name in enabled):
        raise ValueError('Enabled MCP server has no definition in config/mcp.toml')
    rows = []
    for name, definition in definitions.items():
        row = {**definition, 'name': name, 'enabled': name in enabled}
        def expand(value):
            return value.replace('{project}', str(settings.root)).replace('{account_dir}', str(settings.account_directory))
        row['command'] = [expand(part) for part in row.get('command', [])]
        row['cwd'] = expand(row.get('cwd', '{project}'))
        row['env'] = {key: expand(value) for key, value in row.get('env', {}).items()}
        rows.append(row)
    return parse_mcp_servers({'servers': rows})


def parse_mcp_servers(raw) -> list[McpServerConfig]:
    if isinstance(raw, dict):
        servers = raw.get("servers", [])
    elif isinstance(raw, list):
        servers = raw
    else:
        raise ValueError("MCP配置必须是对象或数组")
    if not isinstance(servers, list):
        raise ValueError("MCP配置servers必须是数组")
    result: list[McpServerConfig] = []
    seen: set[str] = set()
    for index, item in enumerate(servers):
        if not isinstance(item, dict):
            raise ValueError(f"mcp server[{index}] 必须是对象")
        name = str(item.get("name", "")).strip()
        if not _NAME.fullmatch(name):
            raise ValueError(f"mcp server[{index}].name 无效: {name}")
        if name in seen:
            raise ValueError(f"mcp server 名称重复: {name}")
        seen.add(name)
        command = item.get("command")
        if not isinstance(command, list) or not command or any(not isinstance(part, str) or not part for part in command):
            raise ValueError(f"mcp server {name}: command 必须是非空字符串数组")
        cwd = item.get("cwd")
        if cwd is not None:
            cwd = str(cwd)
            if not cwd.strip():
                cwd = None
        env_raw = item.get("env") or {}
        if not isinstance(env_raw, dict):
            raise ValueError(f"mcp server {name}: env 必须是对象")
        env = {str(key): _resolve_env(str(value)) for key, value in env_raw.items()}
        allowed = item.get("allowed_tools") or []
        if not isinstance(allowed, list) or any(not isinstance(value, str) for value in allowed):
            raise ValueError(f"mcp server {name}: allowed_tools 必须是字符串数组")
        risk = str(item.get("risk", "write"))
        if risk not in {"read-only", "write", "external-side-effect"}:
            raise ValueError(f"mcp server {name}: risk 无效")
        enabled = item.get("enabled", True)
        timeout = float(item.get("timeout_seconds", 30))
        if not isinstance(enabled, bool):
            raise ValueError(f"mcp server {name}: enabled 必须是布尔值")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError(f"mcp server {name}: timeout_seconds 必须为有限正数")
        result.append(
            McpServerConfig(
                name=name,
                command=tuple(str(part) for part in command),
                cwd=cwd,
                env=env,
                enabled=enabled,
                allowed_tools=tuple(str(value) for value in allowed),
                risk=risk,
                timeout_seconds=timeout,
            )
        )
    return result
