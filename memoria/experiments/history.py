"""Immutable sources and explicitly attributed annotation in one account's space.

These are local administrator tools, not HTTP authentication entry points. No
source, annotation or prediction is written to the production memory database.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .records import digest_file

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
PURPOSES = {"memory_processing", "annotation", "evaluation"}


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid research identifier")
    return value


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing {name}")
    return value


def timestamp(value):
    text(value, "timezone-aware timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Source timestamps must include a timezone")
    return result


def write_json(path, value):
    path.write_text(canonical(value) + "\n", encoding="utf-8")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


class HistoryWorkspace:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.owner = identifier(directory.name)
        self.root = self.path("research")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, *parts):
        target = self.directory.joinpath(*parts)
        # Resolve every descendant: a symlink/junction must never select another account.
        if not target.resolve().is_relative_to(self.directory):
            raise ValueError("Research path escapes account storage")
        for parent in [target, *target.parents]:
            if parent == self.directory:
                break
            if parent.is_symlink():
                raise ValueError("Research symlinks are not allowed")
        return target

    def db(self):
        db = sqlite3.connect(self.path("research", "registry.db"), timeout=10)
        db.executescript("""
            CREATE TABLE IF NOT EXISTS participant_splits (
                participant_id TEXT PRIMARY KEY, split TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS bundles (
                id TEXT PRIMARY KEY, digest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reviews (
                dataset_id TEXT NOT NULL, reviewer TEXT NOT NULL, digest TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(dataset_id,reviewer));
            CREATE TABLE IF NOT EXISTS protocols (id TEXT PRIMARY KEY, digest TEXT NOT NULL);
        """)
        return db

    def _publish(self, relative, files):
        target = self.path("research", *relative)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = target.parent / (".pending-" + uuid.uuid4().hex)
        temporary.mkdir(mode=0o700)
        try:
            for name, value in files.items():
                # Pin exact UTF-8/LF bytes, including on Windows, before hashing.
                (temporary / name).write_bytes(value.encode("utf-8"))
            temporary.rename(target)  # Refuse replacement of an existing bundle.
        finally:
            if temporary.exists():
                if not temporary.resolve().is_relative_to(self.directory):
                    raise ValueError("Temporary path escapes account storage")
                shutil.rmtree(temporary)
        return target

    def import_public(self, dataset_id):
        """Reuse only this account's verified public import, with unknown identities."""
        identifier(dataset_id)
        path = self.path("memory", "memoria.db")
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            row = db.execute("SELECT manifest_json,receipt_json FROM dataset_imports WHERE dataset_id=?", (dataset_id,)).fetchone()
            if not row:
                raise ValueError("No public import in this account")
            source_manifest, receipt = json.loads(row[0]), json.loads(row[1])
            if receipt.get("owner_user_id") != self.owner:
                raise ValueError("Foreign public import receipt")
            # The persisted receipt is not enough: check the fixed source file and every original message.
            subset = self.path("datasets", "lccc-base", f"multiturn-{len(receipt['source_mapping'])}.jsonl")
            if digest_file(subset) != receipt["subset_sha256"]:
                raise ValueError("Public source SHA mismatch")
            originals = [json.loads(line) for line in subset.read_text(encoding="utf-8").splitlines()]
            messages, groups = [], []
            for original, mapping in zip(originals, receipt["source_mapping"], strict=True):
                if original["id"] != mapping["source_id"]:
                    raise ValueError("Source mapping changed")
                group = identifier(mapping["source_id"])
                groups.append({"id": group, "split": "development", "identity": "unknown; source group, not a person"})
                for content, mid in zip(original["dialog"], mapping["message_ids"], strict=True):
                    actual = db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
                    if not actual or actual["session_id"] != mapping["session_id"] or actual["content"] != content:
                        raise ValueError("Public source removed or edited")
                    messages.append({"id": mid, "conversation_id": group, "participant_id": group,
                                     "speaker_id": None, "role": "unknown", "content": content,
                                     "observed_at": None, "content_sha256": hashlib.sha256(content.encode()).hexdigest()})
        finally:
            db.close()
        body = "".join(canonical(item) + "\n" for item in messages)
        manifest = {"schema_version": 1, "dataset_id": dataset_id, "owner_user_id": self.owner,
                    "kind": "public_dialogue_diagnostic", "provenance": source_manifest,
                    "participants": groups, "source_sha256": receipt["subset_sha256"],
                    "history_sha256": hashlib.sha256(body.encode()).hexdigest(),
                    "messages": len(messages), "conversations": len(groups),
                    "imported_at": datetime.now(timezone.utc).isoformat(),
                    "authenticity": "pinned public source; person identity and event time unknown"}
        fingerprint = hashlib.sha256(canonical({k: v for k, v in manifest.items() if k != "imported_at"}).encode()).hexdigest()
        registry = self.db()
        try:
            with registry:
                registry.execute("BEGIN IMMEDIATE")
                old = registry.execute("SELECT digest FROM bundles WHERE id=?", (dataset_id,)).fetchone()
                if old:
                    if old[0] != fingerprint:
                        raise ValueError("Public research bundle changed")
                    self.load(dataset_id)
                    return dataset_id
                self._publish(("sources", dataset_id), {"history.jsonl": body, "manifest.json": canonical(manifest)})
                registry.execute("INSERT INTO bundles VALUES(?,?)", (dataset_id, fingerprint))
        finally:
            registry.close()
        return dataset_id

    def import_history(self, source: Path):
        bundle = read_json(source)
        if bundle.get("kind") != "consented_personal_history" or bundle.get("schema_version") != 1:
            raise ValueError("Expected a real personal history bundle, schema_version=1")
        dataset = identifier(bundle.get("dataset_id"))
        provenance = text(bundle.get("provenance"), "provenance")
        participants = bundle.get("participants")
        if not isinstance(participants, list) or not participants:
            raise ValueError("Participants and consent declarations are required")
        participant_map = {}
        for item in participants:
            pid = identifier(item.get("id"))
            if pid in participant_map or item.get("split") not in {"development", "test"}:
                raise ValueError("Duplicate participant or invalid split")
            consent = item.get("consent", {})
            if consent.get("granted") is not True or set(consent.get("purposes", [])) != PURPOSES:
                raise ValueError("Explicit consent for processing, annotation and evaluation is required")
            timestamp(consent.get("recorded_at"))
            text(consent.get("reference"), "consent reference")
            participant_map[pid] = item
        messages = bundle.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("No source messages")
        ids, previous, conversation_owner = set(), {}, {}
        cleaned = []
        for item in messages:
            mid = identifier(item.get("id"))
            cid = identifier(item.get("conversation_id"))
            pid = identifier(item.get("participant_id"))
            if mid in ids or pid not in participant_map:
                raise ValueError("Duplicate source ID or unconsented participant")
            ids.add(mid)
            if item.get("role") not in {"user", "assistant"}:
                raise ValueError("Only original user/assistant turns are supported")
            speaker = identifier(item.get("speaker_id"))
            if item["role"] == "user" and speaker != pid:
                raise ValueError("User speaker must match the declared participant")
            observed = timestamp(item.get("observed_at"))
            if pid in previous and observed < previous[pid]:
                raise ValueError("Messages must preserve chronological order per participant")
            previous[pid] = observed
            if cid in conversation_owner and conversation_owner[cid] != pid:
                raise ValueError("Do not combine different participants into one conversation")
            conversation_owner[cid] = pid
            content = text(item.get("content"), "source content")
            cleaned.append({"id": mid, "conversation_id": cid, "participant_id": pid,
                            "speaker_id": speaker, "role": item["role"], "content": content,
                            "observed_at": item["observed_at"],
                            "content_sha256": hashlib.sha256(content.encode()).hexdigest()})
        if set(previous) != set(participant_map):
            raise ValueError("Each declared participant must have original messages")
        body = "".join(canonical(item) + "\n" for item in cleaned)
        manifest = {"schema_version": 1, "dataset_id": dataset, "owner_user_id": self.owner,
                    "kind": bundle["kind"], "provenance": provenance, "participants": participants,
                    "source_sha256": digest_file(source),
                    "history_sha256": hashlib.sha256(body.encode()).hexdigest(),
                    "messages": len(cleaned), "conversations": len(conversation_owner),
                    "imported_at": datetime.now(timezone.utc).isoformat(),
                    "authenticity": "declared by source provider; format validation is not independent verification"}
        fingerprint = hashlib.sha256(canonical({k: v for k, v in manifest.items() if k != "imported_at"}).encode()).hexdigest()
        db = self.db()
        try:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT digest FROM bundles WHERE id=?", (dataset,)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise ValueError("Dataset exists with different sources or consent")
                self.load(dataset)
                return dataset
            for pid, item in participant_map.items():
                row = db.execute("SELECT split FROM participant_splits WHERE participant_id=?", (pid,)).fetchone()
                if row and row[0] != item["split"]:
                    raise ValueError("Participant cannot cross development/test splits")
                db.execute("INSERT OR IGNORE INTO participant_splits VALUES(?,?)", (pid, item["split"]))
            target = self._publish(("sources", dataset), {"history.jsonl": body, "manifest.json": canonical(manifest)})
            db.execute("INSERT INTO bundles VALUES(?,?)", (dataset, fingerprint))
            db.commit()
        except BaseException:
            db.rollback()
            # An interrupted publish is intentionally not silently reused on retry.
            raise
        finally:
            db.close()
        return dataset

    def load(self, dataset):
        identifier(dataset)
        manifest_path = self.path("research", "sources", dataset, "manifest.json")
        manifest = read_json(manifest_path)
        history_path = self.path("research", "sources", dataset, "history.jsonl")
        if manifest["owner_user_id"] != self.owner or manifest["dataset_id"] != dataset:
            raise ValueError("Foreign source bundle")
        if digest_file(history_path) != manifest["history_sha256"]:
            raise ValueError("Original history changed")
        db = self.db()
        try:
            old = db.execute("SELECT digest FROM bundles WHERE id=?", (dataset,)).fetchone()
            fingerprint = hashlib.sha256(canonical({k: v for k, v in manifest.items() if k != "imported_at"}).encode()).hexdigest()
            if not old or old[0] != fingerprint:
                raise ValueError("Unregistered or changed provenance/consent")
        finally:
            db.close()
        messages = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines()]
        return manifest, messages

    def template(self, dataset, reviewer):
        manifest, messages = self.load(dataset)
        return {"schema_version": 1, "dataset_id": dataset, "history_sha256": manifest["history_sha256"],
                "reviewer": identifier(reviewer), "status": "draft", "annotation_seconds": None,
                "annotator": {"kind": "unspecified", "tool": None, "independence_id": None},
                "scope": "facts_and_qa", "facts": [], "probes": [], "source_context": messages}

    @staticmethod
    def annotator(review):
        value = review.get("annotator")
        if not isinstance(value, dict) or value.get("kind") not in {"human", "ai_assisted"}:
            raise ValueError("Declare annotator kind: human or ai_assisted")
        if value["kind"] == "human":
            identifier(value.get("independence_id"))
        else:
            text(value.get("tool"), "AI annotation tool")
            if value.get("independence_id") is not None:
                raise ValueError("AI annotation cannot declare an independent human identity")
        return value

    def validate_review(self, review):
        manifest, messages = self.load(review.get("dataset_id"))
        identifier(review.get("reviewer"))
        if review.get("schema_version") != 1 or review.get("status") != "completed":
            raise ValueError("Annotation must explicitly be completed")
        self.annotator(review)
        if review.get("history_sha256") != manifest["history_sha256"]:
            raise ValueError("Annotation was made against different history")
        duration = review.get("annotation_seconds")
        if type(duration) not in {float, int} or not 0 < duration < float("inf"):
            raise ValueError("Record actual annotation elapsed time; AI time is not human labor")
        if "annotation_started_at" in review or "annotation_finished_at" in review:
            elapsed = (timestamp(review.get("annotation_finished_at")) - timestamp(review.get("annotation_started_at"))).total_seconds()
            if elapsed <= 0 or abs(elapsed - duration) > 0.01:
                raise ValueError("Annotation timestamps and elapsed time differ")
        indexed = {item["id"]: (index, item) for index, item in enumerate(messages)}

        def evidence(spans, pid, cutoff=None):
            if not isinstance(spans, list):
                raise ValueError("Evidence must be a list of exact original spans")
            for span in spans:
                if not isinstance(span, dict):
                    raise ValueError("Evidence spans must be objects")
                mid = span.get("message_id")
                if mid not in indexed:
                    raise ValueError("Unknown evidence source")
                index, item = indexed[mid]
                a, b = span.get("start"), span.get("end")
                if item["participant_id"] != pid or (cutoff is not None and index > cutoff):
                    raise ValueError("Foreign or future evidence")
                if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(item["content"]):
                    raise ValueError("Invalid evidence span")
                if span.get("quote") != item["content"][a:b] or span.get("content_sha256") != item["content_sha256"]:
                    raise ValueError("Evidence quote or source SHA differs")
        seen = set()
        participant_ids = {p["id"] for p in manifest["participants"]}
        if not isinstance(review.get("facts"), list):
            raise ValueError("Facts must be a list (empty is allowed)")
        for fact in review.get("facts", []):
            fid = identifier(fact.get("id"))
            if fid in seen:
                raise ValueError("Duplicate fact ID")
            seen.add(fid)
            pid = identifier(fact.get("participant_id"))
            if pid not in participant_ids:
                raise ValueError("Unknown participant/source group")
            text(fact.get("predicate"), "fact predicate")
            text(fact.get("statement"), "source-grounded fact statement")
            if fact.get("modality") not in {"statement", "inference", "plan", "unknown"}:
                raise ValueError("Explicit modality is required")
            if "subject_id" not in fact or "value" not in fact or "conditions" not in fact or "fact_time" not in fact:
                raise ValueError("Keep unknown subject/value/conditions/fact_time explicit as null")
            if fact["subject_id"] not in {None, pid}:
                raise ValueError("This phase supports only participant or unknown fact subjects")
            if manifest["kind"] == "public_dialogue_diagnostic" and (fact["subject_id"] is not None or fact["fact_time"] is not None):
                raise ValueError("Public dialogue subjects and event times must remain unknown")
            if not fact.get("evidence"):
                raise ValueError("Facts require original evidence")
            evidence(fact["evidence"], pid)
            if fact["subject_id"] == pid and not any(indexed[s["message_id"]][1]["role"] == "user" for s in fact["evidence"]):
                raise ValueError("Personal facts require an original participant utterance")
        seen = set()
        if not isinstance(review.get("probes"), list):
            raise ValueError("Probes must be a list")
        if not review["probes"] and review.get("scope") != "extraction_only":
            raise ValueError("At least one source-grounded probe is required (or declare extraction_only)")
        for probe in review["probes"]:
            qid = identifier(probe.get("id"))
            if qid in seen:
                raise ValueError("Duplicate probe ID")
            seen.add(qid)
            cutoff = indexed.get(probe.get("cutoff_message_id"))
            if not cutoff or cutoff[1]["participant_id"] != probe.get("participant_id"):
                raise ValueError("Unknown or foreign checkpoint")
            text(probe.get("question"), "annotated question")
            if type(probe.get("answerable")) is not bool:
                raise ValueError("Answerability requires an explicit annotation decision")
            answers = probe.get("accepted_answers")
            if not isinstance(answers, list) or not answers or any(not isinstance(x, str) or not x.strip() for x in answers):
                raise ValueError("Annotated accepted answers (including abstention wording) are required")
            if probe["answerable"] and not probe.get("evidence"):
                raise ValueError("Answerable probes require evidence")
            evidence(probe.get("evidence"), probe["participant_id"], cutoff[0])
        if "coverage" in review:
            coverage = review["coverage"]
            if not isinstance(coverage, list) or len(coverage) != len(participant_ids):
                raise ValueError("Coverage must audit every source group exactly once")
            covered = set()
            for item in coverage:
                pid = item.get("participant_id")
                expected = [m["id"] for m in messages if m["participant_id"] == pid]
                if pid not in participant_ids or pid in covered or item.get("message_ids") != expected:
                    raise ValueError("Coverage contains missing, duplicate or foreign source messages")
                if item.get("status") != "reviewed":
                    raise ValueError("Coverage has unreviewed sources")
                text(item.get("note"), "source review note")
                covered.add(pid)
        return manifest, messages

    def submit(self, path):
        review = read_json(path)
        self.validate_review(review)
        encoded = canonical(review)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        db = self.db()
        try:
            with db:
                old = db.execute("SELECT digest FROM reviews WHERE dataset_id=? AND reviewer=?",
                                 (review["dataset_id"], review["reviewer"])).fetchone()
                if old and old[0] != digest:
                    raise ValueError("Review is immutable; use a new reviewer revision ID")
                db.execute("INSERT OR IGNORE INTO reviews VALUES(?,?,?,?)",
                           (review["dataset_id"], review["reviewer"], digest, encoded))
        finally:
            db.close()
        return digest

    def freeze(self, dataset, protocol_id, reviewers, adjudication=None):
        identifier(protocol_id)
        if not reviewers or len(set(reviewers)) != len(reviewers):
            raise ValueError("Distinct reviewer revision IDs are required")
        db = self.db()
        try:
            selected = []
            for reviewer in reviewers:
                row = db.execute("SELECT payload,digest FROM reviews WHERE dataset_id=? AND reviewer=?",
                                 (dataset, identifier(reviewer))).fetchone()
                if not row:
                    raise ValueError("Missing completed review")
                selected.append((json.loads(row[0]), row[1]))
        finally:
            db.close()
        review = selected[0][0]
        # A revision ID does not establish a second person. Old reviews must be
        # resubmitted with explicit provenance before making a new freeze.
        for original, _ in selected:
            self.validate_review(original)
        content = lambda r: canonical({"facts": r["facts"], "probes": r["probes"]})
        disagreement = len({content(r) for r, _ in selected}) > 1
        adjudicator = None
        if adjudication:
            review = read_json(adjudication)
            self.validate_review(review)
            if review["dataset_id"] != dataset or review["reviewer"] in reviewers:
                raise ValueError("Adjudicator must independently identify this dataset")
            text(review.get("resolution"), "adjudication resolution")
            adjudicator = {"reviewer": review["reviewer"], "sha256": digest_file(adjudication),
                           "resolution": review["resolution"], "annotation_seconds": review["annotation_seconds"],
                           "annotator": self.annotator(review)}
        elif disagreement:
            raise ValueError("Disagreement requires explicit human adjudication")
        manifest, messages = self.validate_review(review)
        if not review["probes"]:
            raise ValueError("QA freeze requires probes; extraction-only reviews are not QA gold")
        split = {p["id"]: p["split"] for p in manifest["participants"]}
        people = [self.annotator(r)["independence_id"] for r, _ in selected if self.annotator(r)["kind"] == "human"]
        all_human = len(people) == len(selected) and self.annotator(review)["kind"] == "human"
        independent = len(set(people))
        if any(split[p["participant_id"]] == "test" for p in review["probes"]) and (not all_human or independent < 2):
            raise ValueError("Test protocols require two independent human reviews")
        if adjudicator and all_human and adjudicator["annotator"]["independence_id"] in people:
            raise ValueError("Adjudicator must be a different independent human")
        inputs, gold = [], []
        for probe in review["probes"]:
            cutoff = next(i for i, item in enumerate(messages) if item["id"] == probe["cutoff_message_id"])
            # ONLY the original visible prefix and query go into the model file.
            inputs.append({"id": probe["id"], "participant_id": probe["participant_id"],
                           "split": split[probe["participant_id"]], "question": probe["question"],
                           "cutoff_message_id": probe["cutoff_message_id"],
                           "history": [m for m in messages[:cutoff + 1] if m["participant_id"] == probe["participant_id"]]})
            gold.append({k: probe[k] for k in ("id", "participant_id", "answerable", "accepted_answers", "evidence")})
        input_body = "".join(canonical(r) + "\n" for r in inputs)
        gold_body = "".join(canonical(r) + "\n" for r in gold)
        protocol = {"schema_version": 1, "protocol_id": protocol_id, "owner_user_id": self.owner,
                    "dataset_id": dataset, "history_sha256": manifest["history_sha256"],
                    "source_kind": manifest["kind"], "created_at": datetime.now(timezone.utc).isoformat(),
                    "reviewers": [{"id": r["reviewer"], "sha256": sha, "annotation_seconds": r["annotation_seconds"],
                                   "annotator": self.annotator(r)} for r, sha in selected],
                    "disagreement": disagreement, "adjudicator": adjudicator,
                    "human_reviewed": all_human, "independent_human_review_count": independent,
                    "scope": ("AI-assisted development QA gold; independent human review pending" if not all_human else
                              "single-review development diagnostic" if independent < 2 else "human-reviewed protocol; not a quality result"),
                    "input_sha256": hashlib.sha256(input_body.encode()).hexdigest(),
                    "gold_sha256": hashlib.sha256(gold_body.encode()).hexdigest(), "cases": len(inputs)}
        db = self.db()
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT 1 FROM protocols WHERE id=?", (protocol_id,)).fetchone():
                    raise ValueError("Protocol already frozen; use a new protocol ID")
                target = self._publish(("protocols", protocol_id), {"inputs.jsonl": input_body,
                                      "gold.jsonl": gold_body, "protocol.json": canonical(protocol),
                                      "reviews.json": canonical([r for r, _ in selected]),
                                      "adjudication.json": canonical(review if adjudication else None)})
                db.execute("INSERT INTO protocols VALUES(?,?)", (protocol_id, hashlib.sha256(canonical(protocol).encode()).hexdigest()))
            return target
        finally:
            db.close()

    def protocol(self, protocol_id, *, gold=False):
        identifier(protocol_id)
        directory = self.path("research", "protocols", protocol_id)
        protocol = read_json(self.path("research", "protocols", protocol_id, "protocol.json"))
        if protocol["owner_user_id"] != self.owner or protocol["protocol_id"] != protocol_id:
            raise ValueError("Foreign protocol")
        db = self.db()
        try:
            row = db.execute("SELECT digest FROM protocols WHERE id=?", (protocol_id,)).fetchone()
            if not row or row[0] != hashlib.sha256(canonical(protocol).encode()).hexdigest():
                raise ValueError("Unregistered or changed frozen protocol")
        finally:
            db.close()
        self.load(protocol["dataset_id"])
        filename, key = ("gold.jsonl", "gold_sha256") if gold else ("inputs.jsonl", "input_sha256")
        path = self.path("research", "protocols", protocol_id, filename)
        if digest_file(path) != protocol[key]:
            raise ValueError("Frozen protocol artifact changed")
        return protocol, [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()], path
