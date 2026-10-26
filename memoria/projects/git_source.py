"""Read-only git history export. This adapter never writes to the repository.

The log becomes one document-type library source: commit timestamps stay inside
the exported text, and the source itself keeps source_time unknown instead of
borrowing the export time. Truncation is stated in the text, never silent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import ProjectError

_FIELD, _RECORD = "\x1f", "\x1e"
_MAX_TEXT = 118_000
_MAX_BODY_CHARS = 600


def collect_git_history(
    repo_path: str, *, since: str | None = None, max_commits: int = 200, include_body: bool = True
) -> dict:
    """Export recent commits as one document text with provenance metadata."""
    root = Path(repo_path.strip()) if repo_path and repo_path.strip() else None
    if root is None or not root.is_dir():
        raise ProjectError(422, "仓库路径不存在，请先在项目中配置 repo_path")
    if not (root / ".git").exists():
        raise ProjectError(422, "该目录不是 Git 仓库（缺少 .git）")
    if not 1 <= int(max_commits) <= 2000:
        raise ProjectError(422, "max_commits 需在 1-2000 之间")
    if since:
        since = since.strip()
        if not since or len(since) > 64 or any(ch < " " for ch in since):
            raise ProjectError(422, "since 参数不合法")
    args = [
        "git", "-C", str(root), "log",
        f"--max-count={int(max_commits)}",
        f"--pretty=format:%H{_FIELD}%an%x1f%aI{_FIELD}%s{_FIELD}%b{_RECORD}",
    ]
    if since:
        args.append(f"--since={since}")
    try:
        proc = subprocess.run(args, capture_output=True, timeout=30)
    except FileNotFoundError as exc:
        raise ProjectError(422, "未找到 git 命令，无法导出提交历史") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProjectError(504, "git log 执行超时，请缩小 since 范围后重试") from exc
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise ProjectError(422, f"git log 失败：{stderr[:200] or '未知错误'}")
    entries = []
    for raw in proc.stdout.decode("utf-8", "replace").split(_RECORD):
        parts = raw.split(_FIELD)
        if len(parts) < 4:
            continue
        sha, author, date, subject = (part.replace(_FIELD, " ").strip() for part in parts[:4])
        body = parts[4].replace(_FIELD, " ").strip() if len(parts) > 4 else ""
        if sha:
            entries.append({"sha": sha, "author": author, "date": date, "subject": subject, "body": body})
    if not entries:
        raise ProjectError(422, "仓库在指定范围内没有可导出的提交")
    head, oldest = entries[0], entries[-1]
    lines = [
        f"# 项目 Git 历史：{root.name}",
        f"# 提交数：{len(entries)} · 范围：{oldest['sha'][:12]}..{head['sha'][:12]} · "
        "本文是导出时刻的快照；各提交时间以每行日期为准，导出时间不补齐为事件时间。",
    ]
    for entry in entries:
        lines.append(f"- {entry['sha'][:12]} | {entry['author']} | {entry['date']} | {entry['subject']}")
        if include_body and entry["body"]:
            body = entry["body"]
            if len(body) > _MAX_BODY_CHARS:
                body = body[:_MAX_BODY_CHARS] + "…（提交说明截断）"
            lines.append("  " + body.replace("\r\n", "\n").replace("\n", "\n  "))
    text = "\n".join(lines)
    if len(text) > _MAX_TEXT:
        text = text[:_MAX_TEXT] + "\n# （超出单资料长度上限，历史在此截断；可用 since 分批导出）"
    return {
        "text": text,
        "head": head["sha"],
        "oldest": oldest["sha"],
        "count": len(entries),
        "repo_name": root.name,
        "title": f"Git 提交历史：{root.name}（{len(entries)} 个提交）",
    }
