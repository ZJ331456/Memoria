"""Fetch an authentic, pinned LCCC-base subset; import only into an isolated corpus DB."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import urllib.request
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from memoria.store import Store

REVISION = "5bd582fa28cd7143f2f9c852e08e23089d677c44"
BUILDER_REVISION = "9022ba27075f75c2f59d57d7fa5f42e8d1151aec"
FILE = "lccc_base_valid.jsonl.gz"
URL = f"https://huggingface.co/datasets/silver/lccc/resolve/{REVISION}/{FILE}"
DIRECTORY = ROOT / "data/datasets/lccc-base"


def prepare(limit: int):
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    archive = DIRECTORY / FILE
    tree_url = f"https://huggingface.co/api/datasets/silver/lccc/tree/{REVISION}"
    entries = json.load(urllib.request.urlopen(tree_url, timeout=60))
    entry = next(item for item in entries if item["path"] == FILE)
    expected = entry["lfs"]["oid"]
    if not archive.exists():
        partial = archive.with_suffix(".partial")
        urllib.request.urlretrieve(URL, partial)
        if hashlib.sha256(partial.read_bytes()).hexdigest() != expected:
            raise ValueError("source archive SHA256 mismatch")
        partial.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise ValueError("source archive SHA256 mismatch")
    rows = []
    with gzip.open(archive, "rt", encoding="utf-8") as f:
        for index, line in enumerate(f):
            dialog = json.loads(line)
            if not isinstance(dialog, list) or not all(isinstance(t, str) and t.strip() for t in dialog):
                raise ValueError(f"invalid source dialogue {index}")
            if len(dialog) < 3:
                continue
            rows.append({"id": f"lccc-base-validation-{index}", "source_row": index,
                         "dialog": dialog, "speaker_ids": None, "event_times": None})
            if len(rows) == limit:
                break
    if len(rows) != limit:
        raise ValueError("not enough multi-turn source dialogues")
    subset = DIRECTORY / f"multiturn-{limit}.jsonl"
    subset.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    manifest = {"dataset": "LCCC-base", "kind": "human_public_weibo_conversations", "split": "validation",
                "source": "https://github.com/thu-coai/CDial-GPT", "mirror": "silver/lccc",
                "mirror_revision": REVISION, "official_builder_revision": BUILDER_REVISION,
                "url": URL, "archive_sha256": expected, "subset_sha256": hashlib.sha256(subset.read_bytes()).hexdigest(),
                "selection": "first N source rows with at least 3 utterances; no rewriting, no generated labels",
                "dialogues": len(rows), "utterances": sum(len(r["dialog"]) for r in rows),
                "source_rows": [r["source_row"] for r in rows], "license": "MIT (official dataset builder declaration)",
                "scope": "No stable user IDs, timestamps, longitudinal identity or fact/QA labels; not a companion benchmark."}
    subset.with_suffix(".manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return subset, rows, manifest


def import_corpus(subset: Path, rows: list[dict], manifest: dict):
    database = DIRECTORY / f"multiturn-{len(rows)}.db"
    receipt = database.with_suffix(".import.json")
    if database.exists():
        if receipt.exists() and json.loads(receipt.read_text(encoding="utf-8"))["subset_sha256"] == manifest["subset_sha256"]:
            return database
        raise ValueError("Existing corpus DB has no matching receipt; refusing to overwrite")
    imported = []
    with closing(Store(database, "json")) as store:
        for row in rows:
            session = store.create_session(f"公开微博对话 {row['id']}（说话者未知）")
            messages = [store.add_message(session["id"], "user", text) for text in row["dialog"]]
            imported.append({"source_id": row["id"], "session_id": session["id"],
                             "message_ids": [m["id"] for m in messages]})
    receipt.write_text(json.dumps({"subset_sha256": manifest["subset_sha256"], "source_mapping": imported,
                                  "memory_writes": 0, "background_jobs": 0}, ensure_ascii=False, indent=2), encoding="utf-8")
    return database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--import-corpus", action="store_true")
    args = parser.parse_args()
    subset, rows, manifest = prepare(args.limit)
    print(json.dumps({"subset": str(subset), "dialogues": manifest["dialogues"], "utterances": manifest["utterances"]}, ensure_ascii=False))
    if args.import_corpus:
        print("Isolated corpus DB:", import_corpus(subset, rows, manifest))


if __name__ == "__main__":
    main()
