from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from ..observability.tracing import _sanitize


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_endpoint(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme == "local":
        return url
    host = parsed.hostname or ""
    host = f"[{host}]" if ":" in host else host
    return urlunsplit((parsed.scheme, host + (f":{parsed.port}" if parsed.port else ""), parsed.path, "", ""))


def code_identity(root: Path) -> dict:
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL)
    diff = git("diff", "HEAD", "--binary")
    untracked = git("ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")
    return {"commit": git("rev-parse", "HEAD").decode().strip(),
            "branch": git("branch", "--show-current").decode().strip(),
            "dirty": bool(diff or any(untracked)), "dirty_diff_sha256": hashlib.sha256(diff).hexdigest(),
            "changed_files": git("diff", "HEAD", "--name-only", "-z").decode().split("\0")[:-1],
            "untracked": [{"path": name, "sha256": digest_file(root / name)} for name in untracked if name]}


@lru_cache(maxsize=64)
def _model_file_digest(path: str, size: int, mtime: int) -> str:
    # Reuse within this run only. A fresh process hashes actual files again.
    return digest_file(Path(path))


def model_snapshot(path: Path, root: Path) -> dict:
    if not path.is_dir():
        return {"available": False}
    files = sorted({*path.glob("*.safetensors"), *path.glob("*.json"), *path.glob("*.txt"), *path.glob("*.jinja")})
    manifest = {}
    download = path / "download_manifest.json"
    if download.is_file():
        original = json.loads(download.read_text(encoding="utf-8"))
        manifest = {k: original.get(k) for k in ("model_id", "requested_revision", "revision_pinning", "verification")}
    artifacts = []
    for file in files:
        if file.name == "download_manifest.json":
            continue
        stat = file.stat()
        artifacts.append({"file": file.name, "bytes": stat.st_size,
                          "sha256": _model_file_digest(str(file), stat.st_size, stat.st_mtime_ns)})
    tokenizer = {f["file"]: f["sha256"] for f in artifacts if f["file"].startswith(("tokenizer", "vocab", "special_tokens", "added_tokens", "chat_template"))}
    return {"available": True, "path": path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path),
            "revision": manifest, "artifacts": artifacts,
            "tokenizer_sha256": hashlib.sha256(json.dumps(tokenizer, sort_keys=True).encode()).hexdigest()}


def environment() -> dict:
    packages = {}
    for name in ("torch", "transformers", "numpy", "httpx", "fastapi", "openai", "sqlite-vec", "pytest"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    gpu = None
    try:
        proc = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
        if proc.returncode == 0:
            gpu = [{"name": r[0].strip(), "driver": r[1].strip(), "vram_mib": int(r[2])} for r in csv.reader(proc.stdout.splitlines())]
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return {"python": platform.python_version(), "executable": os.path.basename(os.sys.executable),
            "venv": os.sys.prefix != os.sys.base_prefix, "platform": platform.platform(),
            "cpu": platform.processor(), "logical_cpus": os.cpu_count(), "gpu": gpu, "packages": packages}


class RunRecorder:
    """Unique run directory, append-only per-case logs, final hashes and error denominators."""
    def __init__(self, output_root: Path, root: Path, *, datasets: list[Path], models: dict,
                 protocol: dict, seed: int | None, model_paths: dict[str, Path] | None = None):
        self.started = time.perf_counter()
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        self.path = output_root / self.run_id
        self.path.mkdir(parents=True, exist_ok=False)
        self.manifest = {"schema_version": 1, "run_id": self.run_id,
            "started_at_utc": datetime.now(timezone.utc).isoformat(), "status": "running",
            "code": code_identity(root), "environment": environment(), "seed": seed,
            "seed_scope": "recorded protocol identifier; source order is fixed; no provider sampling seed is asserted",
            "datasets": [{"file": p.name, "sha256": digest_file(p)} for p in datasets],
            "models": models, "model_snapshots": {role: model_snapshot(p, root) for role, p in (model_paths or {}).items()},
            "model_binding": "client-declared local snapshots; HTTP provider identity requires deployment provenance",
            "protocol": protocol, "artifacts": {}}
        self.manifest["manifest_build_ms"] = round((time.perf_counter() - self.started) * 1000, 3)
        self._save_manifest()

    def _save_manifest(self):
        target = self.path / "manifest.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(_sanitize(self.manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(target)

    def append(self, filename: str, row: dict):
        if filename not in {"cases.jsonl", "events.jsonl", "calls.jsonl", "prompts.jsonl"}:
            raise ValueError("unknown run artifact")
        with (self.path / filename).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_sanitize(row), ensure_ascii=False, allow_nan=False) + "\n")

    def finish(self, metrics: dict, *, error: str | None = None):
        (self.path / "metrics.json").write_text(json.dumps(_sanitize(metrics), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        self.manifest.update(status="failed" if error or metrics.get("errors", 0) else "completed",
            finished_at_utc=datetime.now(timezone.utc).isoformat(),
            elapsed_ms=round((time.perf_counter() - self.started) * 1000, 3), error=error)
        self.manifest["artifacts"] = {p.name: {"sha256": digest_file(p), "bytes": p.stat().st_size}
                                      for p in self.path.iterdir() if p.name != "manifest.json" and p.is_file()}
        self._save_manifest()
