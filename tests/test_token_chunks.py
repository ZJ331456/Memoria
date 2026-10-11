from __future__ import annotations

import asyncio
import json
from copy import deepcopy

import httpx
import pytest

from eval.longmemeval import system_input
from eval.run_longmemeval import Options, TrackedEmbedder, document_vectors, run
from memoria.memory.token_chunks import TokenChunker
from memoria.memory.local_bge import EmbeddingInputTooLong, LocalBgeEncoder
from memoria.llm.server import _inference_error
from tests.test_longmemeval import configured, fixture_case


class CharacterTokenizer:
    def __call__(self, text, **kwargs):
        assert kwargs.get("truncation") is False
        return {"input_ids": [0, *range(len(text)), 1]}


@pytest.mark.parametrize("text", ["", " \n\t", "用户喜欢红茶。" * 100 + "尾部事实：现在住在上海。", "😀abc\r\n北京" * 80])
def test_chunks_preserve_source_and_include_dynamic_headers(text):
    chunker = TokenChunker(CharacterTokenizer(), max_tokens=51, max_chars=200)
    prefix = lambda part: f"source=P{part:03}; date=2026-10-11\n"
    chunks = chunker.split(text, prefix)
    assert "".join(c.content[len(prefix(i)):] for i, c in enumerate(chunks)) == text
    assert all(c.tokens == len(c.content) + 2 <= 51 for c in chunks)
    coverage = chunker.coverage(text, chunks)
    assert coverage["covered_chars"] == coverage["source_chars"] == len(text)
    assert coverage["truncated_chunks"] == 0
    assert chunks[-1].end == len(text)


def test_headers_without_room_fail_instead_of_looping():
    chunker = TokenChunker(CharacterTokenizer(), 8)
    with pytest.raises(ValueError, match="header exceeds"):
        chunker.split("body", lambda part: "long header")
    with pytest.raises(ValueError, match="no room"):
        chunker.split("body", lambda part: "123456")


def test_long_history_headers_and_labels_are_isolated():
    case = fixture_case()
    case["haystack_sessions"][1][0]["content"] = "我喜欢红茶。" * 100 + "尾部事实：住在上海。"
    chunker = TokenChunker(CharacterTokenizer(), 128)
    clean = system_input(case, chunker=chunker)
    changed = deepcopy(case)
    changed.update(answer="LEAK_SECRET", answer_session_ids=["filler"], question_type="knowledge-update")
    changed["haystack_sessions"][1][0]["has_answer"] = False
    assert system_input(changed, chunker=chunker) == clean
    docs = [d for d in clean.documents if (d.session_index, d.turn_index) == (1, 0)]
    assert docs[-1].content.endswith("住在上海。")
    assert all(d.tokens <= 128 and d.id in d.content and "session_date=" in d.content for d in docs)
    assert sum(d.end - d.start for d in docs) == len(case["haystack_sessions"][1][0]["content"])


def test_keyless_local_eval_configuration_is_ready(tmp_path, monkeypatch):
    settings = configured(tmp_path)
    settings.embedding.model = "bge-small-zh-v1.5"
    settings.embedding.base_url = "http://127.0.0.1:8080/v1"
    settings.embedding.api_key = ""
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps([fixture_case()]))
    monkeypatch.setattr("eval.run_longmemeval.local_chunker", lambda *a, **kw: TokenChunker(CharacterTokenizer(), 512))

    async def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(TrackedEmbedder, "_request", embed)
    report = asyncio.run(run(path, settings, Options(embedding=True)))
    assert report["metrics"]["overall"]["errors"] == 0
    coverage = report["results"][0]["encoding_coverage"]
    assert coverage["covered_chars"] == coverage["source_chars"]
    assert report["protocol"]["chunking"]["token_limit_verified"]


def test_cache_changes_with_encoder_namespace(tmp_path, monkeypatch):
    calls = []

    async def embed(self, texts):
        calls.append(texts)
        return [[1.0, 2.0] for _ in texts]

    monkeypatch.setattr(TrackedEmbedder, "embed_batch", embed)
    first = TrackedEmbedder(configured(tmp_path))
    first.namespace = "model-and-tokenizer-sha-v1"
    asyncio.run(document_vectors(first, ["same content"], tmp_path / "cache"))
    second = TrackedEmbedder(configured(tmp_path))
    second.namespace = "model-and-tokenizer-sha-v2"
    _, cached = asyncio.run(document_vectors(second, ["same content"], tmp_path / "cache"))
    assert not cached and len(calls) == 2


def test_public_index_vectors_stay_in_active_namespace_without_read_backfill(tmp_path, monkeypatch):
    settings = configured(tmp_path)
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps([fixture_case()]))
    calls = []

    async def embed(self, texts):
        calls.append(texts)
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(TrackedEmbedder, "_request", embed)
    report = asyncio.run(run(path, settings, Options(embedding=True)))
    assert report["metrics"]["overall"]["errors"] == 0
    assert len(calls) == 2  # one index batch, one query; no document re-encoding
    assert calls[1] == [fixture_case()["question"]]


def test_client_never_silently_discards_text_after_8000_chars(tmp_path, monkeypatch):
    text = "长" * 8000 + "TAIL_MUST_REACH_PROVIDER"
    seen = []

    def respond(request):
        seen.extend(json.loads(request.content)["input"])
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0]}]})

    original = httpx.AsyncClient
    monkeypatch.setattr("memoria.memory.embedding.httpx.AsyncClient", lambda **kw:
        original(transport=httpx.MockTransport(respond), **kw))
    asyncio.run(TrackedEmbedder(configured(tmp_path)).embed(text))
    assert seen == [text]


def test_bge_overflow_is_rejected_before_forward_or_device_transfer(tmp_path, monkeypatch):
    encoder = LocalBgeEncoder(tmp_path, "cpu")

    class Mask:
        def sum(self, **kwargs):
            return self

        def tolist(self):
            return [513]

    def tokenize(texts, **kwargs):
        assert kwargs["truncation"] is False
        return {"attention_mask": Mask()}

    monkeypatch.setattr(encoder, "_load", lambda: None)
    encoder._tokenizer = tokenize
    try:
        with pytest.raises(EmbeddingInputTooLong, match="513 tokens"):
            encoder._encode(["long source"])
    finally:
        encoder._executor.shutdown(wait=True)
    status, error = _inference_error(EmbeddingInputTooLong("too long"))
    assert status == 400 and error["error"]["param"] == "input"
