from memoria.config import ModelConfig
from memoria.memory.embedding import EmbeddingClient

import asyncio
import json

import httpx
import pytest

from memoria.memory.token_chunks import TokenChunker
from memoria.memory.local_bge import EmbeddingInputTooLong, LocalBgeEncoder
from memoria.llm.server import _inference_error


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










def test_client_never_silently_discards_text_after_8000_chars(tmp_path, monkeypatch):
    text = "长" * 8000 + "TAIL_MUST_REACH_PROVIDER"
    seen = []

    def respond(request):
        seen.extend(json.loads(request.content)["input"])
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0]}]})

    original = httpx.AsyncClient
    monkeypatch.setattr("memoria.memory.embedding.httpx.AsyncClient", lambda **kw:
        original(transport=httpx.MockTransport(respond), **kw))
    asyncio.run(EmbeddingClient(ModelConfig(model="test", base_url="https://embedding.example.test/v1", api_key="test")).embed(text))
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
