"""Install pinned MCP servers into a project-local runtime, never into base."""
from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".mcp"
VERSION = "2.0.2"
ASSET = "github-mcp-server_Windows_x86_64.zip"


def main():
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit("This installer targets Windows x86_64; use the corresponding official binary on other OSes.")
    RUNTIME.mkdir(exist_ok=True)
    node = shutil.which("node")
    npm = shutil.which("npm.cmd")
    if not node or not npm:
        raise SystemExit("Install Node.js first; npm.cmd and node must be on PATH.")
    node_version = subprocess.check_output([node, "--version"], text=True).strip()
    if int(node_version.lstrip("v").split(".")[0]) < 20:
        raise SystemExit("Node.js 20 or later is required.")
    node_dir = RUNTIME / "node"
    node_dir.mkdir(exist_ok=True)
    source = ROOT / "deploy/mcp"
    shutil.copy2(source / "package.json", node_dir / "package.json")
    lock = source / "package-lock.json"
    if lock.exists():
        shutil.copy2(lock, node_dir / lock.name)
    npm_cli = Path(npm).parent / "node_modules/npm/bin/npm-cli.js"
    if not npm_cli.is_file():
        raise SystemExit("Cannot locate npm-cli.js beside npm.cmd")
    subprocess.run([node, str(npm_cli), "ci" if lock.exists() else "install", "--global=false", "--no-fund", "--no-audit"], cwd=node_dir, check=True)
    if not lock.exists():
        shutil.copy2(node_dir / "package-lock.json", lock)
    base = f"https://github.com/github/github-mcp-server/releases/download/v{VERSION}/"
    sums = urllib.request.urlopen(base + f"github-mcp-server_{VERSION}_checksums.txt", timeout=60).read().decode()
    expected = next(line.split()[0] for line in sums.splitlines() if line.split()[-1] == ASSET)
    archive = RUNTIME / ASSET
    urllib.request.urlretrieve(base + ASSET, archive)
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit("GitHub release checksum mismatch")
    target = RUNTIME / "github"
    target.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for name in ("github-mcp-server.exe", "LICENSE"):
            if name in z.namelist():
                (target / name).write_bytes(z.read(name))
    if not (target / "github-mcp-server.exe").exists():
        raise SystemExit("Official archive does not contain expected executable")
    manifest = {"github": {"version": VERSION, "url": base + ASSET, "sha256": actual},
                "search": "open-websearch@2.2.0", "feishu": "@larksuiteoapi/lark-mcp@0.5.1", "node": node, "node_version": node_version}
    (RUNTIME / "installation.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Installed into", RUNTIME)


if __name__ == "__main__":
    main()
