"""Private terminal credential entry followed by official Feishu browser OAuth."""
from __future__ import annotations

import argparse
import getpass
import json
import subprocess
import os
from pathlib import Path

from mcp_server import ROOT, command
from memoria.private_files import load_private_json, save_private_json
from user_paths import directory_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials-file", type=Path, help="Private JSON with app_id and app_secret")
    parser.add_argument("--login-only", action="store_true")
    parser.add_argument("--user", required=True)
    parser.add_argument("--config")
    args = parser.parse_args()
    directory = directory_for(args.user, args.config)
    target = directory / "secrets/feishu.credentials.dpapi"
    if args.credentials_file:
        data = json.loads(args.credentials_file.read_text(encoding="utf-8-sig"))
    elif args.login_only:
        data = load_private_json(target)
    else:
        data = {"app_id": input("Feishu App ID: ").strip(), "app_secret": getpass.getpass("Feishu App Secret (hidden): ").strip()}
    if not data.get("app_id", "").startswith("cli_") or not data.get("app_secret"):
        raise SystemExit("Missing or invalid Feishu credentials")
    save_private_json(target, {"app_id": data["app_id"], "app_secret": data["app_secret"]})
    os.environ["MEMORIA_FEISHU_CREDENTIALS"] = str(target)
    os.environ["MEMORIA_FEISHU_PROFILE"] = str(directory / "secrets/feishu-profile")
    argv, env = command("feishu", login=True)
    print("Set your application's redirect URL to http://localhost:3000/callback; complete authorization in your browser.")
    completed = subprocess.run(argv, env=env, cwd=ROOT)
    if completed.returncode:
        raise SystemExit("Official OAuth login did not complete; check app publication, scopes and callback URL.")
    config = directory / "config/mcp_servers.json"
    if config.exists():
        settings = json.loads(config.read_text(encoding="utf-8"))
        for server in settings["servers"]:
            if server["name"] == "feishu":
                server["enabled"] = True
        config.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    print("OAuth completed. Verify with scripts/check_mcp.py --user USERNAME --server feishu; reload MCP after logging in as that account.")


if __name__ == "__main__":
    main()
