"""Fetch pinned, authentic LCCC conversations and assign them to a named user's space."""
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
from memoria.store import Store, now
from scripts.user_paths import directory_for

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


def import_corpus(subset: Path, rows: list[dict], manifest: dict, directory: Path):
    if hashlib.sha256(subset.read_bytes()).hexdigest() != manifest["subset_sha256"]:
        raise ValueError("subset SHA256 mismatch")
    dataset_id = f"lccc-base-validation-multiturn-{len(rows)}"
    database = directory / "memory/memoria.db"
    receipt_file = directory / f"datasets/lccc-base/multiturn-{len(rows)}.import.json"
    with closing(Store(database, "json")) as store:
        previous = store.db.execute("SELECT subset_sha256,receipt_json FROM dataset_imports WHERE dataset_id=?", (dataset_id,)).fetchone()
        if previous:
            if previous["subset_sha256"] != manifest["subset_sha256"]:
                raise ValueError("Existing dataset differs; refusing to overwrite")
            receipt = json.loads(previous["receipt_json"])
        else:
            imported = []
            timestamp = now()  # Ingestion time, not the unknown source event time.
            with store.lock, store.db:
                for row in rows:
                    sid = hashlib.sha256(f"{dataset_id}/{row['id']}".encode()).hexdigest()[:32]
                    title = f"公开微博对话 {row['id']}（说话者未知）"
                    store.db.execute("INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)", (sid, title, timestamp, timestamp))
                    mids = []
                    for ordinal, text in enumerate(row["dialog"]):
                        mid = hashlib.sha256(f"{sid}/{ordinal}".encode()).hexdigest()[:32]
                        store.db.execute("INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)", (mid, sid, "user", text, timestamp))
                        store.db.execute("INSERT INTO messages_fts(id,content) VALUES(?,?)", (mid, text))
                        mids.append(mid)
                    imported.append({"source_id": row["id"], "source_row": row["source_row"], "session_id": sid, "message_ids": mids})
                receipt = {"dataset_id": dataset_id, "owner_user_id": directory.name,
                           "subset_sha256": manifest["subset_sha256"], "source_mapping": imported,
                           "memory_writes": 0, "background_jobs": 0,
                           "speaker_ids": None, "event_times": None, "imported_at": timestamp}
                store.db.execute("INSERT INTO dataset_imports VALUES(?,?,?,?,?)", (dataset_id, manifest["subset_sha256"], json.dumps(manifest, ensure_ascii=False), json.dumps(receipt, ensure_ascii=False), timestamp))
        # Verify the persisted receipt against all original utterances on reruns.
        for row, mapping in zip(rows, receipt["source_mapping"], strict=True):
            actual = [store.message_source(mid) for mid in mapping["message_ids"]]
            if any(m is None or m["session_id"] != mapping["session_id"] for m in actual) or [m["content"] for m in actual] != row["dialog"]:
                raise ValueError("Imported source was removed or edited; refusing duplicate import")
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    receipt_file.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    (receipt_file.parent / subset.name).write_bytes(subset.read_bytes())
    (receipt_file.parent / subset.with_suffix(".manifest.json").name).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--user", help="Existing account that owns the imported conversations")
    parser.add_argument("--config")
    args = parser.parse_args()
    directory = directory_for(args.user, args.config) if args.user else None
    subset, rows, manifest = prepare(args.limit)
    print(json.dumps({"subset": str(subset), "dialogues": manifest["dialogues"], "utterances": manifest["utterances"]}, ensure_ascii=False))
    if directory:
        print("User corpus DB:", import_corpus(subset, rows, manifest, directory))


if __name__ == "__main__":
    main()
