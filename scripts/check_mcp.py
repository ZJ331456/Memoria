"""Check authentic MCP handshakes and read calls; never seed a demo server."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from memoria.mcp.client import McpClient
from memoria.mcp.config import configured_mcp_servers
from memoria.config import Settings
from memoria.models_config import apply_account_settings
from user_paths import directory_for


async def check(server):
    client = McpClient(server.name, server.command, cwd=server.cwd, env=server.env)
    row = {"server": server.name, "initialized": False, "business_call_ok": False, "tool_names": [], "error": None}
    try:
        tools = await client.connect()
        row.update(initialized=True, tool_names=[t.name for t in tools])
        if server.name == "search":
            result = await client.call("search", {"query": "长期陪伴 记忆管理", "limit": 3, "engines": ["baidu"]}, timeout=60)
            parsed = json.loads(result)
            results = parsed.get("results", [])
            if not results or any(not r.get("url", "").startswith(("http://", "https://")) for r in results):
                raise ValueError("Search did not return actual result URLs")
            row.update(query=parsed["query"], result_count=len(results),
                       result_links=[{"title": r["title"], "url": r["url"]} for r in results])
        elif server.name == "github":
            me = json.loads(await client.call("get_me", {}, timeout=60))
            # SDK versions may nest the identity response in a user object.
            login = me.get("login") or me.get("user", {}).get("login")
            if login != "ZJ331456":
                raise ValueError("GitHub authenticated identity is not ZJ331456")
            row["login"] = login
            result = await client.call("get_file_contents", {"owner": "ZJ331456", "repo": "Memoria", "path": "README.md", "ref": "refs/heads/main"}, timeout=60)
            if "README" not in result and "Memoria" not in result:
                raise ValueError("GitHub repository read returned no expected content")
            row["repository_read"] = True
        elif server.name == "feishu":
            info = next((t for t in tools if "space_list" in t.name.lower()), None)
            if not info:
                raise ValueError("Feishu wiki space list tool not found")
            result = await client.call(info.name, {"useUAT": True}, timeout=60)
            payload = json.loads(result)
            # Official MCP unwraps successful API data; explicit API errors
            # must fail, while an empty but authorized workspace is valid.
            if "code" in payload:
                if payload["code"] != 0:
                    raise ValueError("Feishu API error")
                payload = payload.get("data", {})
            if not isinstance(payload.get("items"), list) or not isinstance(payload.get("has_more"), bool):
                raise ValueError("Feishu did not return a wiki space list")
            row.update(response_valid=True, space_count=len(payload["items"]))
            drive = json.loads(await client.call("drive_v1_file_list", {"useUAT": True}, timeout=60))
            if "code" in drive:
                if drive["code"] != 0:
                    raise ValueError("Feishu drive API error")
                drive = drive.get("data", {})
            if not isinstance(drive.get("files"), list):
                raise ValueError("Feishu did not return a drive file list")
            row["drive_file_count"] = len(drive["files"])
        else:
            raise ValueError("No real business check defined for this server")
        row["business_call_ok"] = True
    except Exception as exc:
        # Do not publish arbitrary third-party errors which may contain tokens.
        row["error"] = f"{type(exc).__name__}: check failed; authorization/network/tool response needs inspection"
    finally:
        await client.disconnect()
    return row


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", choices=["search", "github", "feishu"])
    parser.add_argument("--user", required=True)
    parser.add_argument("--config")
    args = parser.parse_args()
    directory = directory_for(args.user, args.config)
    servers = configured_mcp_servers(apply_account_settings(Settings.load(args.config), directory))
    targets = [s for s in servers if (s.name == args.server if args.server else s.enabled)]
    if not targets:
        raise SystemExit("No matching servers; run configure_mcp.py first")
    rows = list(await asyncio.gather(*(check(s) for s in targets)))
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "servers": rows}
    out = directory / "reports/mcp-check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if any(not r["business_call_ok"] for r in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
