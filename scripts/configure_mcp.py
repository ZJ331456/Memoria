"""Generate the local stdio server configuration; no secrets or dynamic npx installs."""
import json
from pathlib import Path
import argparse
from user_paths import directory_for

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--config")
    args = parser.parse_args()
    directory = directory_for(args.user, args.config)
    output = directory / "config/mcp_servers.json"
    servers = [{"name": name, "command": [str(ROOT / ".venv/Scripts/python.exe"), str(ROOT / "scripts/mcp_server.py"), name],
                "cwd": str(ROOT), "enabled": name != "feishu", "risk": "read-only", "timeout_seconds": 60}
               for name in ("search", "github", "feishu")]
    servers[0]["allowed_tools"] = ["search", "fetchWebContent"]
    servers[1]["allowed_tools"] = ["get_me", "get_file_contents", "search_repositories", "search_code", "list_branches", "list_commits", "issue_read", "pull_request_read"]
    servers[2]["allowed_tools"] = ["docx_v1_document_rawContent", "drive_v1_file_list", "wiki_v2_space_list", "wiki_v2_space_getNode"]
    servers[2]["env"] = {"MEMORIA_FEISHU_CREDENTIALS": str(directory / "secrets/feishu.credentials.dpapi"),
                         "MEMORIA_FEISHU_PROFILE": str(directory / "secrets/feishu-profile")}
    # The Feishu launcher restricts the official server to four read APIs.
    if output.exists():
        previous = json.loads(output.read_text(encoding="utf-8"))["servers"]
        by_name = {s["name"]: s for s in previous}
        for server in servers:
            if server["name"] in by_name:
                server["enabled"] = by_name[server["name"]].get("enabled", server["enabled"])
        servers += [s for s in previous if s["name"] not in {x["name"] for x in servers}
                    and not s["command"][0].startswith(("/root/", "/usr/"))
                    and s["name"] != "demo"]
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.write_text(json.dumps({"servers": servers}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Local MCP configuration:", output)


if __name__ == "__main__":
    main()
