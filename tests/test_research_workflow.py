"""Temporary engineering fixtures only, never research subjects or results."""
import asyncio
import hashlib
import json
import subprocess
from pathlib import Path

import httpx
import pytest

from memoria.config import Settings
from memoria.experiments.history import HistoryWorkspace, read_json, write_json
from memoria.experiments.roles import ExperimentLLM
from memoria.experiments.runner import execute, load_run, recover_run, score, score_template, select_history
from scripts.research import private_input


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=fixture", "-c", "user.email=fixture@example.test",
                    "commit", "--allow-empty", "-qm", "fixture"], check=True)
    directory = root / "data/users" / ("a" * 32)
    workspace = HistoryWorkspace(directory)
    cfg = root / "config.toml"
    cfg.write_text('[llm.main]\nmodel="fixture"\nbase_url="http://127.0.0.1:8080/v1"\n', encoding="utf-8")
    settings = Settings.load(cfg)
    settings.root = root
    source = root / "source.json"
    write_json(source, {"schema_version": 1, "kind": "consented_personal_history", "dataset_id": "engineering",
                        "provenance": "temporary engineering fixture, not research data",
                        "participants": [{"id": "subject", "split": "development", "consent": {
                            "granted": True, "purposes": ["memory_processing", "annotation", "evaluation"],
                            "recorded_at": "2026-01-01T00:00:00Z", "reference": "temporary-fixture"}}],
                        "messages": [
                            {"id": "m1", "conversation_id": "c1", "participant_id": "subject", "speaker_id": "subject",
                             "role": "user", "observed_at": "2026-01-01T01:00:00Z", "content": "测试前缀证据"},
                            {"id": "m2", "conversation_id": "c2", "participant_id": "subject", "speaker_id": "subject",
                             "role": "user", "observed_at": "2026-01-02T01:00:00Z", "content": "future-content-never-seen"}]})
    workspace.import_history(source)
    return workspace, settings, source


def review(workspace, reviewer="human1"):
    result = workspace.template("engineering", reviewer)
    _, messages = workspace.load("engineering")
    item = messages[0]
    span = {"message_id": "m1", "start": 0, "end": len(item["content"]), "quote": item["content"],
            "content_sha256": item["content_sha256"]}
    result.update(status="completed", annotation_seconds=3, facts=[], probes=[{
        "id": "q1", "participant_id": "subject", "cutoff_message_id": "m1", "question": "测试前缀是什么？",
        "answerable": True, "accepted_answers": ["gold-only-marker"], "evidence": [span]}])
    return result


def freeze(workspace, source):
    path = source.parent / "review.json"
    write_json(path, review(workspace))
    workspace.submit(path)
    workspace.freeze("engineering", "p1", ["human1"])


def factory(handler):
    def make(settings, roles, recorder, timings):
        client = ExperimentLLM(settings, roles, recorder, timings)
        client.transport = httpx.MockTransport(handler)
        return client
    return make


def response(answer):
    return httpx.Response(200, text='data: ' + json.dumps({"choices": [{"delta": {"content": json.dumps(answer, ensure_ascii=False)}, "finish_reason": "stop"}]}) + '\n\ndata: [DONE]\n\n', headers={"content-type": "text/event-stream"})


def test_sources_immutable_and_participant_split_not_leaked(setup):
    workspace, _, source = setup
    assert workspace.import_history(source) == "engineering"
    data = read_json(source)
    data["dataset_id"] = "other"
    data["participants"][0]["split"] = "test"
    write_json(source, data)
    with pytest.raises(ValueError, match="cannot cross"):
        workspace.import_history(source)
    assert not workspace.path("research/sources/other").exists()
    history = workspace.path("research/sources/engineering/history.jsonl")
    history.write_text(history.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        workspace.load("engineering")


@pytest.mark.parametrize("alter", ["consent", "timezone", "order", "speaker"])
def test_rejects_unconsented_or_rewritten_identity_inputs(setup, alter):
    workspace, _, source = setup
    data = read_json(source)
    data["dataset_id"] = "rejected"
    if alter == "consent":
        data["participants"][0]["consent"]["granted"] = False
    elif alter == "timezone":
        data["messages"][0]["observed_at"] = "2026-01-01T01:00:00"
    elif alter == "order":
        data["messages"].reverse()
    else:
        data["messages"][0]["speaker_id"] = "different"
    write_json(source, data)
    with pytest.raises(ValueError):
        workspace.import_history(source)
    assert not workspace.path("research/sources/rejected").exists()


@pytest.mark.parametrize("alter", ["quote", "sha", "future", "empty_answer", "time", "draft"])
def test_review_requires_real_evidence_and_checkpoint(setup, alter):
    workspace, _, _ = setup
    value = review(workspace)
    span = value["probes"][0]["evidence"][0]
    if alter == "quote": span["quote"] = "not-source"
    elif alter == "sha": span["content_sha256"] = "0" * 64
    elif alter == "future": span["message_id"] = "m2"
    elif alter == "empty_answer": value["probes"][0]["accepted_answers"] = []
    elif alter == "time": value["annotation_seconds"] = float("nan")
    else: value["status"] = "draft"
    with pytest.raises(ValueError):
        workspace.validate_review(value)


def test_independent_reviews_disagreement_and_adjudication(setup):
    workspace, _, source = setup
    for reviewer in ("h1", "h2"):
        value = review(workspace, reviewer)
        if reviewer == "h2": value["probes"][0]["accepted_answers"] = ["other-label"]
        path = source.parent / f"{reviewer}.json"
        write_json(path, value)
        workspace.submit(path)
    with pytest.raises(ValueError, match="Disagreement"):
        workspace.freeze("engineering", "disputed", ["h1", "h2"])
    adjudication = review(workspace, "h3")
    adjudication["resolution"] = "temporary-fixture arbitration"
    path = source.parent / "adjudication.json"
    write_json(path, adjudication)
    frozen = workspace.freeze("engineering", "resolved", ["h1", "h2"], path)
    protocol = read_json(frozen / "protocol.json")
    assert protocol["disagreement"] and protocol["adjudicator"]["reviewer"] == "h3"
    assert "gold-only-marker" not in (frozen / "inputs.jsonl").read_text()
    assert "future-content-never-seen" not in (frozen / "inputs.jsonl").read_text()


def test_runner_only_sees_prefix_then_separate_score(setup):
    workspace, settings, source = setup
    freeze(workspace, source)
    def handler(request):
        payload = request.content.decode()
        assert "gold-only-marker" not in payload and "future-content-never-seen" not in payload
        return response({"answer": "gold-only-marker", "evidence_ids": ["m1"]})
    run = asyncio.run(execute(workspace, settings, mode="qa", protocol_id="p1", client_factory=factory(handler)))
    _, manifest, cases = load_run(workspace, run.name)
    assert manifest["status"] == "completed"
    assert cases[0]["presented_ids"] == ["m1"]
    assert manifest["protocol"]["mode"] == "qa"
    assert not workspace.path("memory/memoria.db").exists()
    scores = read_json(score(workspace, run.name))
    assert scores["exact_answer_rate_all_cases"] == 1
    assert scores["human_unknown_cases"] == 1
    assert scores["cases"][0]["evidence_message_recall"] == 1


def test_errors_preserve_all_cases_and_replay_uses_fresh_run(setup):
    workspace, settings, source = setup
    value = review(workspace)
    value["probes"].append({**value["probes"][0], "id": "q2"})
    path = source.parent / "review.json"
    write_json(path, value)
    workspace.submit(path)
    workspace.freeze("engineering", "p1", ["human1"])
    handler = lambda request: httpx.Response(500, json={"error": "temporary fault"})
    runs = [asyncio.run(execute(workspace, settings, mode="qa", protocol_id="p1", client_factory=factory(handler))) for _ in range(2)]
    assert runs[0] != runs[1]
    _, manifest, cases = load_run(workspace, runs[0].name)
    assert manifest["status"] == "failed" and len(cases) == 2
    scores = read_json(score(workspace, runs[0].name))
    assert scores["requested_cases"] == 2 and scores["exact_answer_rate_all_cases"] == 0


def test_cancelled_and_unattempted_denominator_is_recorded(setup):
    workspace, settings, source = setup
    value = review(workspace)
    value["probes"].append({**value["probes"][0], "id": "q2"})
    path = source.parent / "review.json"
    write_json(path, value)
    workspace.submit(path)
    workspace.freeze("engineering", "p1", ["human1"])
    def handler(request): raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(execute(workspace, settings, mode="qa", protocol_id="p1", client_factory=factory(handler)))
    run = next(workspace.path("experiments").iterdir())
    _, manifest, cases = load_run(workspace, run.name)
    assert manifest["status"] == "failed"
    assert [c["status"] for c in cases] == ["cancelled", "not_attempted"]


def test_frozen_tampering_and_cross_account_sources_rejected(setup):
    workspace, _, source = setup
    freeze(workspace, source)
    other = HistoryWorkspace(workspace.directory.parent / ("b" * 32))
    with pytest.raises(FileNotFoundError): other.protocol("p1")
    with pytest.raises(ValueError, match="another account"):
        private_input(workspace.path("research/protocols/p1/gold.jsonl"), other.directory)
    protocol_path = workspace.path("research/protocols/p1/protocol.json")
    changed = read_json(protocol_path)
    changed["input_sha256"] = "0" * 64
    write_json(protocol_path, changed)
    with pytest.raises(ValueError, match="changed frozen"):
        workspace.protocol("p1")


def test_bm25_selects_prefix_without_hidden_recent_fallback():
    history = [{"id": "a", "content": "项目记忆处理"}, {"id": "b", "content": "完全其他内容"}]
    assert select_history(history, "记忆处理", "B2_fts", 8) == [history[0]]
    assert select_history(history, "xy", "B2_fts", 8) == []


def test_extraction_human_support_requires_matching_source_and_keeps_unknown(setup):
    workspace, settings, source = setup
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": '[{"content":"engineering-only","kind":"fact","importance":3}]'}, "finish_reason": "stop"}]})
    run = asyncio.run(execute(workspace, settings, mode="extract", dataset="engineering", client_factory=factory(handler)))
    human = score_template(workspace, run.name)
    assert len(human["judgments"]) == 2 and human["judgments"][0]["reviewed"] is None
    human.update(reviewer="reviewer", annotation_seconds=7)
    first = human["judgments"][0]
    first["reviewed"] = True
    first["candidate_judgments"][0]["supported"] = True
    path = source.parent / "human-scores.json"
    write_json(path, human)
    with pytest.raises(ValueError, match="original source spans"):
        score(workspace, run.name, path)
    original = first["source_context"][0]
    first["candidate_judgments"][0]["evidence"] = [{"message_id": original["id"], "start": 0,
        "end": len(original["content"]), "quote": original["content"], "content_sha256": original["content_sha256"]}]
    write_json(path, human)
    result = read_json(score(workspace, run.name, path))
    assert result["human_reviewed_cases"] == 1 and result["human_unreviewed_cases"] == 1
    assert result["decisions"]["supported"]["true"] == 1 and result["decisions"]["supported"]["unknown"] == 1


@pytest.mark.parametrize("payload", ['{}', '[1]', '[{"content":"x"}]', '[{"content":"x","kind":"fact","importance":true}]'])
def test_invalid_extraction_is_error_not_legal_empty(setup, payload):
    workspace, settings, _ = setup
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": payload}, "finish_reason": "stop"}]})
    run = asyncio.run(execute(workspace, settings, mode="extract", dataset="engineering", client_factory=factory(handler)))
    _, manifest, cases = load_run(workspace, run.name)
    assert manifest["status"] == "failed"
    assert all(c["status"] == "error" for c in cases)


def test_test_protocol_requires_two_independent_reviews(setup):
    workspace, _, source = setup
    data = read_json(source)
    data["dataset_id"] = "testsource"
    data["participants"][0].update(id="test_subject", split="test")
    for message in data["messages"]:
        message.update(participant_id="test_subject", speaker_id="test_subject")
    write_json(source, data)
    workspace.import_history(source)
    value = review(workspace)
    manifest, _ = workspace.load("testsource")
    value.update(dataset_id="testsource", history_sha256=manifest["history_sha256"])
    value["probes"][0]["participant_id"] = "test_subject"
    path = source.parent / "review.json"
    write_json(path, value)
    workspace.submit(path)
    with pytest.raises(ValueError, match="two independent"):
        workspace.freeze("testsource", "testprotocol", ["human1"])


def test_grounded_protocol_accepts_exact_span_and_rejects_invented_subject(setup):
    workspace, settings, _ = setup
    calls = [0]
    def handler(request):
        payload = json.loads(request.content)
        history = json.loads(payload["messages"][1]["content"])
        message = history[0]
        subject = None if calls[0] == 0 else "invented-person"
        calls[0] += 1
        result = [{"content": "engineering-only", "kind": "fact", "importance": 3, "subject_id": subject,
                   "fact_time": None, "evidence": [{"message_id": message["id"], "start": 0,
                   "end": len(message["content"]), "quote": message["content"]}]}]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}, "finish_reason": "stop"}]})
    run = asyncio.run(execute(workspace, settings, mode="extract", dataset="engineering", extraction_protocol="source_grounded", client_factory=factory(handler)))
    _, _, cases = load_run(workspace, run.name)
    assert [c["status"] for c in cases] == ["success", "error"]
    assert len(cases[0]["candidates"][0]["evidence"][0]["content_sha256"]) == 64


def test_remote_does_not_call_without_explicit_permission(setup):
    from memoria.config import ModelConfig
    workspace, settings, _ = setup
    settings.main = ModelConfig("remote", "private-fixture-key", "https://provider.example.test/v1")
    with pytest.raises(ValueError, match="allow-remote"):
        asyncio.run(execute(workspace, settings, mode="extract", dataset="engineering"))
    assert not workspace.path("experiments").exists()


def test_process_recovery_does_not_fabricate_completion_or_elapsed_time(setup):
    workspace, settings, _ = setup
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "[]"}, "finish_reason": "stop"}]})
    run = asyncio.run(execute(workspace, settings, mode="extract", dataset="engineering", client_factory=factory(handler)))
    manifest = read_json(run / "manifest.json")
    manifest["status"] = "running"
    write_json(run / "manifest.json", manifest)
    with pytest.raises(ValueError, match="still active"):
        recover_run(workspace, run.name, confirm_stopped=True)
    manifest["process_id"] = 2147483647
    write_json(run / "manifest.json", manifest)
    (run / "cases.jsonl").write_text('', encoding='utf-8')
    recover_run(workspace, run.name, confirm_stopped=True)
    _, recovered, cases = load_run(workspace, run.name)
    assert recovered["status"] == "failed" and recovered["elapsed_ms"] is None
    assert all(c["status"] == "interrupted_unknown" for c in cases)


@pytest.mark.parametrize("stream", [False, True])
def test_deepseek_thinking_is_explicit_and_not_sent_to_other_hosts(setup, stream):
    from memoria.config import ModelConfig
    from memoria.llm import LLMClient
    workspace, settings, _ = setup
    settings.main = ModelConfig("deepseek-fixture", "private-fixture-key", "https://api.deepseek.com/v1", "disabled")
    def handler(request):
        assert json.loads(request.content)["thinking"] == {"type": "disabled"}
        if stream:
            return response({"answer": "engineering", "evidence_ids": []})
        return httpx.Response(200, json={"choices": [{"message": {"content": "engineering"}, "finish_reason": "stop"}]})
    client = LLMClient(settings, httpx.MockTransport(handler))
    call = client.chat_stream if stream else client.chat
    asyncio.run(call([{"role": "user", "content": "engineering-only"}]))
    settings.main.base_url = "https://other.example.test/v1"
    with pytest.raises(ValueError, match="official DeepSeek"):
        asyncio.run(call([{"role": "user", "content": "engineering-only"}]))


def test_encrypted_profile_binding_rejects_other_account_and_endpoint(setup, monkeypatch):
    from memoria.models_config import apply_overrides
    workspace, settings, _ = setup
    settings.model_override_file = workspace.path("config/models.override.toml")
    saved = {"owner_user_id": workspace.owner, "model": "deepseek-fixture", "api_key": "private-fixture-key",
             "base_url": "https://api.deepseek.com/v1", "thinking": "disabled"}
    monkeypatch.setattr("memoria.models_config.load_private_json", lambda path: saved)
    section = {"model": saved["model"], "base_url": saved["base_url"], "credential_profile": "deepseek", "api_key": ""}
    apply_overrides(settings, {"llm": {"main": section}})
    assert settings.main.thinking == "disabled" and settings.main.api_key == "private-fixture-key"
    with pytest.raises(ValueError, match="mismatch"):
        apply_overrides(settings, {"llm": {"main": {**section, "base_url": "https://other.example.test/v1"}}})
    saved["owner_user_id"] = "b" * 32
    with pytest.raises(ValueError, match="mismatch"):
        apply_overrides(settings, {"llm": {"main": section}})


def test_grounded_overlapping_quote_is_ambiguous():
    from memoria.experiments.runner import extract_grounded
    from memoria.llm.types import ChatResult
    class Client:
        async def call(self, *args):
            return ChatResult(content=json.dumps([{"content": "engineering", "kind": "fact", "importance": 1,
                "subject_id": None, "fact_time": None, "evidence": [{"quote": "aaa"}]}]))
    with pytest.raises(ValueError, match="one exact"):
        asyncio.run(extract_grounded(Client(), [{"id": "fixture", "role": "unknown", "content": "aaaa"}]))


def test_public_source_receipt_is_verified_and_unknown_kept(tmp_path):
    from contextlib import closing
    from memoria.store import Store
    from scripts.prepare_real_dialogues import import_corpus
    workspace = HistoryWorkspace(tmp_path / "users" / ("a" * 32))
    subset = tmp_path / "source.jsonl"
    rows = [{"id": "real-source-id-fixture", "source_row": 1, "dialog": ["工程测试一", "工程测试二", "工程测试三"]}]
    subset.write_text(json.dumps(rows[0], ensure_ascii=False) + "\n", encoding="utf-8")
    manifest = {"subset_sha256": hashlib.sha256(subset.read_bytes()).hexdigest()}
    import_corpus(subset, rows, manifest, workspace.directory)
    dataset = "lccc-base-validation-multiturn-1"
    # import_corpus uses the subset filename verbatim; the real import uses this canonical name.
    subset_private = workspace.path("datasets/lccc-base/source.jsonl")
    subset_private.rename(workspace.path("datasets/lccc-base/multiturn-1.jsonl"))
    workspace.import_public(dataset)
    workspace.import_public(dataset)
    m, messages = workspace.load(dataset)
    assert m["kind"] == "public_dialogue_diagnostic" and all(x["role"] == "unknown" for x in messages)
    assert all(x["observed_at"] is None and x["speaker_id"] is None for x in messages)
    with closing(Store(workspace.path("memory/memoria.db"), "json")) as store:
        store.db.execute("UPDATE messages SET content='changed'")
        store.db.commit()
    with pytest.raises(ValueError, match="edited"):
        workspace.import_public(dataset)
