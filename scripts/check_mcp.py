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
from memoria.mcp.config import load_mcp_servers


async def check(server):
    client = McpClient(server.name, server.command, cwd=server.cwd, env=server.env)
    row = {"server": server.name, "initialized": False, "business_call_ok": False, "tool_names": [], "error": None}
    try:
        tools = await client.connect()
        row.update(initialized=True, tool_names=[t.name for t in tools])
        if server.name == "search":
            result = await client.call("search", {"query": "小模型 长期记忆", "limit": 3, "engines": ["baidu"]}, timeout=60)
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
            result = await client.call("get_file_contents", {"owner": "ZJ331456", "repo": "Memoria-Agent", "path": "README.md", "ref": "refs/heads/main"}, timeout=60)
            if "README" not in result and "Memoria" not in result:
                raise ValueError("GitHub repository read returned no expected content")
            row["repository_read"] = True
        elif server.name == "feishu":
            info = next((t for t in tools if "space_list" in t.name.lower()), None)
            if not info:
                raise ValueError("Feishu wiki space list tool not found")
            result = await client.call(info.name, {"useUAT": True}, timeout=60)
            payload = json.loads(result)
            if payload.get("code") != 0:
                raise ValueError("Feishu API did not return code 0")
            row["api_code"] = payload["code"]
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
    args = parser.parse_args()
    servers = load_mcp_servers(ROOT / "data/mcp_servers.json")
    targets = [s for s in servers if (s.name == args.server if args.server else s.enabled)]
    if not targets:
        raise SystemExit("No matching servers; run configure_mcp.py first")
    rows = list(await asyncio.gather(*(check(s) for s in targets)))
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "servers": rows}
    out = ROOT / "data/reports/mcp-check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if any(not r["business_call_ok"] for r in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
