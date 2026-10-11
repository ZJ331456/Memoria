"""Synthetic BGE source-coverage check; no production DB, LLM, or quality score."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from memoria.memory.local_bge import MAX_LENGTH, EmbeddingInputTooLong, get_encoder, model_fingerprint, model_path
from memoria.memory.token_chunks import chunking_metadata, local_chunker


async def check(args):
    chunker = local_chunker(args.model)
    path = model_path(args.model)
    fingerprint = model_fingerprint(path)
    encoder = get_encoder(str(path), args.device, "embedding-local:" + fingerprint)
    fixtures = [
        ("zh-tail", "我喜欢红茶，周末在公园散步。" * 150 + "最新事实：2026年10月搬到上海。"),
        ("mixed-tail", "A shared memory: 北京 café 😀\n" * 150 + "Current preference: no coffee after 18:00."),
    ]
    rows = []
    for name, text in fixtures:
        prefix = lambda part: f"source={name}:P{part:03d}; date=2026-10-11; role=user\n"
        chunks = chunker.split(text, prefix)
        assert "".join(c.content[len(prefix(i)):] for i, c in enumerate(chunks)) == text
        assert all(c.tokens <= MAX_LENGTH for c in chunks)
        coverage = chunker.coverage(text, chunks)
        started = time.perf_counter()
        vectors = []
        for start in range(0, len(chunks), 10):
            vectors.extend(await encoder.encode([c.content for c in chunks[start:start + 10]], 120))
        assert len(vectors) == len(chunks) and all(len(v) == 512 for v in vectors)
        row = {"fixture": name, **coverage, "vectors": len(vectors),
               "encode_ms": round((time.perf_counter() - started) * 1000, 3)}
        try:
            await encoder.encode([prefix(0) + text], 120)
        except EmbeddingInputTooLong:
            row["unsplit_overflow_rejected"] = True
        else:
            raise AssertionError("long unsplit input was silently accepted")
        rows.append(row)
    diff = subprocess.check_output(["git", "diff", "HEAD", "--binary"], cwd=ROOT)
    return {"scope": "synthetic source coverage and real BGE encoding; not a memory QA/latency benchmark",
            "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
            "untracked_files": subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"],
                                                        cwd=ROOT, text=True).splitlines(),
            "python": platform.python_version(), "embedding_fingerprint": fingerprint,
            "chunking": chunking_metadata(args.model), "encoder": encoder.public_status(), "results": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="model/bge-small-zh-v1.5")
    parser.add_argument("--device", choices=["cpu", "cuda", "auto"], default="cuda")
    parser.add_argument("--out", type=Path, default=ROOT / "data/reports/token_chunking_smoke.json")
    args = parser.parse_args()
    report = asyncio.run(check(args))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.out), "fixtures": len(report["results"]),
        "covered_chars": sum(r["covered_chars"] for r in report["results"]),
        "chunks": sum(r["chunks"] for r in report["results"]), "truncated_chunks": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
