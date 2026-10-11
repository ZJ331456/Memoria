"""Launch real MCP runtimes without placing credentials in command arguments."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".mcp"
FEISHU_TOOLS = "docx.v1.document.rawContent,drive.v1.file.list,wiki.v2.space.list,wiki.v2.space.getNode"
FEISHU_SCOPE = "offline_access docx:document:readonly drive:drive:readonly wiki:wiki:readonly"


def command(server: str, login: bool = False):
    env = os.environ.copy()
    hidden = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    if server == "search":
        node = shutil.which("node")
        if not node:
            raise ValueError("Node.js is missing")
        env.update(MODE="stdio", DEFAULT_SEARCH_ENGINE="baidu", ALLOWED_SEARCH_ENGINES="baidu,brave,bing,sogou", SEARCH_MODE="request")
        return [node, str(RUNTIME / "node/node_modules/open-websearch/build/index.js")], env
    if server == "github":
        if not env.get("GITHUB_PERSONAL_ACCESS_TOKEN"):
            git = shutil.which("git")
            if not git:
                raise ValueError("git is missing")
            credential = subprocess.run([git, "credential", "fill"], input="protocol=https\nhost=github.com\nusername=ZJ331456\n\n",
                capture_output=True, text=True, timeout=60, cwd=ROOT, env={**env, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}, **hidden)
            fields = dict(line.split("=", 1) for line in credential.stdout.splitlines() if "=" in line)
            if credential.returncode or not fields.get("password"):
                raise ValueError("GitHub credential unavailable; sign in to Git Credential Manager or set GITHUB_PERSONAL_ACCESS_TOKEN")
            env["GITHUB_PERSONAL_ACCESS_TOKEN"] = fields["password"]
        return [str(RUNTIME / "github/github-mcp-server.exe"), "stdio", "--read-only", "--toolsets", "context,repos,issues,pull_requests"], env
    if server == "feishu":
        secret_file = RUNTIME / "feishu.credentials.json"
        if secret_file.exists():
            secret = json.loads(secret_file.read_text(encoding="utf-8-sig"))
            env["APP_ID"], env["APP_SECRET"] = secret["app_id"], secret["app_secret"]
        if not env.get("APP_ID") or not env.get("APP_SECRET"):
            raise ValueError("Feishu credentials missing; run scripts/configure_feishu.py in your terminal")
        node = shutil.which("node")
        if not node:
            raise ValueError("Node.js is missing")
        args = [node, str(RUNTIME / "node/node_modules/@larksuiteoapi/lark-mcp/dist/cli.js")]
        if login:
            args += ["login", "--scope", FEISHU_SCOPE, "--host", "localhost", "--port", "3000"]
        else:
            args += ["mcp", "--oauth", "--scope", FEISHU_SCOPE, "--token-mode", "user_access_token", "--tools", FEISHU_TOOLS, "--language", "zh"]
        env["LARK_DOMAIN"] = "https://open.feishu.cn"
        return args, env
    raise ValueError("Unknown MCP server")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: mcp_server.py search|github|feishu")
    args, env = command(sys.argv[1])
    # Inherit stdio end to end. The parent controls lifetime through stdin EOF;
    # finally terminates the real process when the launcher receives a signal.
    child = subprocess.Popen(args, env=env, cwd=ROOT,
                             stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
                             **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
    try:
        raise SystemExit(child.wait())
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)


if __name__ == "__main__":
    main()
