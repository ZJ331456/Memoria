"""Read-only, tenant-bound experiment metadata. No prompts, keys, gold or job execution."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

RUN_ID = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")
METRICS = {"automatic_memory_writes", "calls", "errors", "quality", "requested_cases", "statuses", "usage_known_calls", "usage_unknown_calls"}
PROTOCOL = {"baseline", "mode", "scope", "dataset_id", "history_limit", "history_sha256", "context_char_budget", "budget_unit", "limit", "selection", "source_kind"}


def numeric_tree(value):
    if type(value) is float and not math.isfinite(value):
        return None
    if value is None or type(value) in (int, float, bool):
        return value
    if isinstance(value, dict):
        return {k: (None if k.startswith(("human", "semantic", "preference")) else numeric_tree(v))
                for k, v in value.items() if isinstance(v, dict) or v is None or type(v) in (int, float, bool)}
    return None


def read_json(root: Path, relative: Path):
    path = root / relative
    # Refuse symlinks/junctions at every level and all reads outside this owner.
    for parent in [path, *path.parents]:
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise ValueError("linked artifact")
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_size > 1_048_576:
        raise ValueError("unavailable artifact")
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest(), len(raw)


def run_summary(root: Path, run_id: str, owner: str) -> dict:
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run ID")
    m, digest, _ = read_json(root, Path(run_id) / "manifest.json")
    if m.get("run_id") != run_id or m.get("protocol", {}).get("owner_user_id") != owner:
        raise ValueError("owner metadata mismatch")
    code = m.get("code", {})
    return {"run_id": run_id, "status": m.get("status"), "started_at_utc": m.get("started_at_utc"),
            "finished_at_utc": m.get("finished_at_utc"), "elapsed_ms": m.get("elapsed_ms"), "manifest_sha256": digest,
            "code": {k: code.get(k) for k in ("commit", "dirty", "branch")},
            "datasets": [{"file": d.get("file"), "sha256": d.get("sha256")} for d in m.get("datasets", [])],
            "protocol": {k: v for k, v in m.get("protocol", {}).items() if k in PROTOCOL},
            "metrics_artifact": m.get("artifacts", {}).get("metrics.json"),
            "models": {role: {k: spec.get(k) for k in ("model", "temperature", "top_p", "max_tokens", "thinking")} for role, spec in m.get("models", {}).items() if isinstance(spec, dict)},
            "quality_boundary": "recorded automatic diagnostics, not independent human or longitudinal quality"}


def research_runs_router(root: Path | None, owner: str) -> APIRouter:
    router = APIRouter(tags=["read-only research records"])

    @router.get("/api/research-runs")
    def listing(limit: int = Query(default=100, ge=1, le=200)):
        if root is None or not root.is_dir():
            return {"runs": [], "unavailable": 0, "read_only": True}
        rows, unavailable = [], 0
        for path in sorted(root.iterdir(), reverse=True):
            if not RUN_ID.fullmatch(path.name):
                continue
            if len(rows) + unavailable >= limit:
                break
            try:
                rows.append(run_summary(root, path.name, owner))
            except (ValueError, OSError, TypeError, KeyError):
                unavailable += 1
        return {"runs": rows, "unavailable": unavailable, "read_only": True}

    @router.get("/api/research-runs/{run_id}")
    def detail(run_id: str):
        try:
            if root is None:
                raise ValueError("no owner experiments")
            summary = run_summary(root, run_id, owner)
            recorded = summary.pop("metrics_artifact")
            metrics, digest, size = read_json(root, Path(run_id) / "metrics.json")
            if not recorded or digest != recorded.get("sha256") or size != recorded.get("bytes"):
                raise ValueError("artifact fingerprint mismatch")
            return {**summary, "metrics": {k: numeric_tree(v) for k, v in metrics.items() if k in METRICS},
                    "metrics_sha256": digest, "artifact_verified": True, "human_metrics_omitted": True,
                    "human_evaluation": "not inferred from automatic scores"}
        except (ValueError, OSError, TypeError, KeyError) as exc:
            raise HTTPException(404, "该账号的完整评测记录不存在、不可用或指纹不匹配") from exc

    return router
