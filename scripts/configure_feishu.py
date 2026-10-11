"""Private terminal credential entry followed by official Feishu browser OAuth."""
from __future__ import annotations

import argparse
import getpass
import json
import subprocess
from pathlib import Path

from mcp_server import ROOT, RUNTIME, command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials-file", type=Path, help="Private JSON with app_id and app_secret")
    parser.add_argument("--login-only", action="store_true")
    args = parser.parse_args()
    target = RUNTIME / "feishu.credentials.json"
    RUNTIME.mkdir(exist_ok=True)
    if args.credentials_file:
        data = json.loads(args.credentials_file.read_text(encoding="utf-8-sig"))
    elif args.login_only:
        data = json.loads(target.read_text(encoding="utf-8-sig"))
    else:
        data = {"app_id": input("Feishu App ID: ").strip(), "app_secret": getpass.getpass("Feishu App Secret (hidden): ").strip()}
    if not data.get("app_id", "").startswith("cli_") or not data.get("app_secret"):
        raise SystemExit("Missing or invalid Feishu credentials")
    target.write_text(json.dumps({"app_id": data["app_id"], "app_secret": data["app_secret"]}), encoding="utf-8")
    argv, env = command("feishu", login=True)
    print("Set your application's redirect URL to http://localhost:3000/callback; complete authorization in your browser.")
    completed = subprocess.run(argv, env=env, cwd=ROOT)
    if completed.returncode:
        raise SystemExit("Official OAuth login did not complete; check app publication, scopes and callback URL.")
    config = ROOT / "data/mcp_servers.json"
    if config.exists():
        settings = json.loads(config.read_text(encoding="utf-8"))
        for server in settings["servers"]:
            if server["name"] == "feishu":
                server["enabled"] = True
        config.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    print("OAuth command completed. Verify with scripts/check_mcp.py --server feishu, then POST /api/mcp/reload.")


if __name__ == "__main__":
    main()
