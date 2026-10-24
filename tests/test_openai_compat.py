from __future__ import annotations

import asyncio
import base64
import json
import struct
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from memoria.config import ModelConfig, Settings
from memoria.llm import LLMClient, ContextLengthError, ProviderError
from memoria.llm.backends.process_engine import LocalCausalLM, LocalQueueFullError, VisibleTextFilter, parse_local_completion
from memoria.llm.server import create_local_openai_app
from memoria.memory.embedding import EmbeddingClient


def settings(tmp_path):
    path = tmp_path / "test.toml"
    path.write_text(f'[storage]\ndatabase="{(tmp_path / "test.db").as_posix()}"\n'
                    '[llm.main]\nmodel="Qwen3.5-2B"\nbase_url="http://127.0.0.1:8080/v1"\n'
                    '[llm]\nmax_retries=0\n[memory.markdown]\nenabled=false\n', encoding="utf-8")
    return Settings.load(path)


class StubEngine:
    def __init__(self):
        self.requests = []
        self.result = {"content": "pong", "tool_calls": [],
                       "raw_message": {"role": "assistant", "content": "pong"},
                       "finish_reason": "stop", "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}}
        self.error = None

    def public_status(self):
        return {"loaded": False}

    async def chat(self, messages, tools, max_tokens, timeout, **options):
        self.requests.append((messages, tools, max_tokens, options))
        if self.error:
            raise self.error
        return self.result

    async def stream_chat(self, *args, **kwargs):
        yield {"result": await self.chat(*args, **kwargs)}


@pytest.fixture
def service(monkeypatch):
    engine = StubEngine()
    monkeypatch.setattr("memoria.llm.server.get_local_llm", lambda *args: engine)
    return create_local_openai_app(embedding_model="model/bge-small-zh-v1.5"), engine


@pytest.mark.parametrize("patch", [
    {"max_tokens": 0}, {"max_tokens": "8"}, {"temperature": -1}, {"top_p": 0},
    {"n": 2}, {"stream": "false"}, {"stop": ["end"]}, {"tool_choice": "required"},
    {"messages": []}, {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]},
    {"max_tokens": 8, "max_completion_tokens": 8},
    {"tools": [{"type": "function", "function": {"name": 123}}]},
])
def test_invalid_options_are_openai_errors_without_inference(service, patch):
    app, engine = service
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json={"model": "Qwen3.5-2B", "messages": [{"role": "user", "content": "ping"}], **patch})
    assert response.status_code == 400 and response.json()["error"]["type"] == "invalid_request_error"
    assert not engine.requests


def test_model_identity_parameters_and_error_mapping(service):
    app, engine = service
    with TestClient(app) as client:
        payload = {"model": "Qwen3.5-2B", "messages": [{"role": "user", "content": "ping"}],
                   "max_completion_tokens": 7, "temperature": 0.7, "top_p": 0.8, "tool_choice": "none"}
        first = client.post("/v1/chat/completions", json=payload).json()
        second = client.post("/v1/chat/completions", json=payload).json()
        assert first["id"] != second["id"]
        assert first["model"] == "model/Qwen3.5-2B"
        assert engine.requests[0][2:] == (7, {"temperature": 0.7, "top_p": 0.8})
        wrong = client.post("/v1/chat/completions", json={**payload, "model": "missing"})
        assert wrong.status_code == 404 and wrong.json()["error"]["code"] == "model_not_found"
        assert client.post("/v1/chat/completions", content="{").status_code == 400
        for error, status, code in [(ContextLengthError("context"), 400, "context_length_exceeded"),
                                    (LocalQueueFullError("queue"), 429, "rate_limit_exceeded"),
                                    (RuntimeError("本地 Embedding 队列已满"), 429, "rate_limit_exceeded"),
                                    (asyncio.TimeoutError(), 504, "timeout")]:
            engine.error = error
            response = client.post("/v1/chat/completions", json=payload)
            assert response.status_code == status and response.json()["error"]["code"] == code


def test_http_round_trip_keeps_two_tools_usage_and_finish(service, tmp_path):
    app, engine = service
    raw_calls = [{"id": f"call{i}", "type": "function", "function": {"name": name, "arguments": json.dumps({"value": i})}}
                 for i, name in enumerate(["calculate", "recall"])]
    engine.result.update(content="", raw_message={"role": "assistant", "content": None, "tool_calls": raw_calls},
                         finish_reason="tool_calls")
    result = asyncio.run(LLMClient(settings(tmp_path), httpx.ASGITransport(app=app)).chat_stream([
        {"role": "user", "content": "two tools"}]))
    assert [call["name"] for call in result.tool_calls] == ["calculate", "recall"]
    assert [call["arguments"] for call in result.tool_calls] == [{"value": 0}, {"value": 1}]
    assert result.finish_reason == "tool_calls" and result.usage["total_tokens"] == 3
    with TestClient(app) as client:
        body = client.post("/v1/chat/completions", json={"model": "Qwen3.5-2B", "messages": [{"role": "user", "content": "ping"}], "stream": True}).text
        events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: {")]
        assert all("usage" not in event for event in events)


def test_embedding_float_base64_and_query_instruction(service, monkeypatch):
    app, _ = service
    seen = []

    class Encoder:
        async def encode(self, texts, timeout):
            seen.extend(texts)
            return [[1.0, *([0.0] * 511)] for _ in texts]

        def count_tokens(self, texts):
            return 3 * len(texts)

    monkeypatch.setattr("memoria.memory.local_bge.get_encoder", lambda *args: Encoder())
    with TestClient(app) as api:
        assert len(api.get("/v1/models").json()["data"]) == 2
        body = {"model": "bge-small-zh-v1.5", "input": ["茶", "猫"]}
        floats = api.post("/v1/embeddings", json=body).json()
        assert floats["usage"]["total_tokens"] == 6
        encoded = api.post("/v1/embeddings", json={**body, "encoding_format": "base64"}).json()
        assert struct.unpack("<512f", base64.b64decode(encoded["data"][0]["embedding"])) == tuple(floats["data"][0]["embedding"])
        assert api.post("/v1/embeddings", json={**body, "dimensions": 384}).status_code == 400
    original = httpx.AsyncClient
    monkeypatch.setattr("memoria.memory.embedding.httpx.AsyncClient", lambda **kwargs: original(transport=httpx.ASGITransport(app=app), **kwargs))
    client = EmbeddingClient(ModelConfig("bge-small-zh-v1.5", "", "http://127.0.0.1:8080/v1"))
    assert client.enabled

    async def scenario():
        assert len(await client.embed_query("饮料")) == 512
        await client.embed("喝茶")

    asyncio.run(scenario())
    assert seen[-2:] == ["为这个句子生成表示以用于检索相关文章：饮料", "喝茶"]


def test_embedding_token_overflow_returns_openai_input_error(service, monkeypatch):
    from memoria.memory.local_bge import EmbeddingInputTooLong
    app, _ = service

    class Encoder:
        async def encode(self, texts, timeout):
            raise EmbeddingInputTooLong("input[0] has 513 tokens; BGE limit is 512")

    monkeypatch.setattr("memoria.memory.local_bge.get_encoder", lambda *args: Encoder())
    with TestClient(app) as api:
        response = api.post("/v1/embeddings", json={"model": "bge-small-zh-v1.5", "input": "long text"})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "context_length_exceeded"
        assert response.json()["error"]["param"] == "input"


def test_truncated_stream_is_not_retried_and_reasoning_is_not_answer(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')

    with pytest.raises(ProviderError, match="部分内容"):
        asyncio.run(LLMClient(settings(tmp_path), httpx.MockTransport(handler)).chat_stream([{ "role": "user", "content": "ping"}]))
    assert len(calls) == 1
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": [{"message": {"content": None, "reasoning_content": "private"}, "finish_reason": "length"}]}))
    result = asyncio.run(LLMClient(settings(tmp_path), transport).chat([{ "role": "user", "content": "ping"}]))
    assert result.content == "" and result.raw_message["reasoning_content"] == "private" and result.finish_reason == "length"


def test_split_thinking_tags_and_thought_tools_never_escape():
    visible = VisibleTextFilter()
    fragments = ["好<th", "ink>secret<tool_call>not executable</tool_call></thi", "nk>的<tool_", "call>hidden</tool_call>答复"]
    assert "".join(visible.feed(text) for text in fragments) + visible.feed("", final=True) == "好的答复"
    text = '<think><tool_call><function=bad></function></tool_call></think>回答'
    assert parse_local_completion(text) == ("回答", [])
    assert parse_local_completion("<think>unfinished") == ("", [])


@pytest.mark.parametrize("stream", [False, True])
def test_length_limited_tools_preserve_finish_without_executing(tmp_path, stream):
    call = {"id": "incomplete", "type": "function", "function": {"name": "calculate", "arguments": '{"expression":'}}
    if stream:
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, **call}]}, "finish_reason": "length"}]}
        response = httpx.Response(200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n")
    else:
        response = httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [call]}, "finish_reason": "length"}]})
    client = LLMClient(settings(tmp_path), httpx.MockTransport(lambda _: response))
    invoke = client.chat_stream if stream else client.chat
    result = asyncio.run(invoke([{"role": "user", "content": "calculate"}]))
    assert result.finish_reason == "length" and result.tool_calls == []


def test_local_stream_arrives_before_completion_and_cancels_worker(tmp_path):
    engine = LocalCausalLM(tmp_path, "cpu")
    released, cancelled = Event(), Event()

    def generate(messages, tools, limit, temperature, top_p, cancel, on_text):
        if on_text:
            on_text("first")
        while not released.wait(0.005):
            if cancel.is_set():
                cancelled.set()
                break
        return {"content": "first", "raw_message": {"content": "first"}, "tool_calls": [], "usage": {}, "finish_reason": "stop"}

    engine._generate = generate

    async def scenario():
        stream = engine.stream_chat([], None, 8, 1)
        assert await asyncio.wait_for(anext(stream), 1) == {"delta": "first"}
        assert not released.is_set()
        await stream.aclose()
        assert await asyncio.to_thread(cancelled.wait, 1)

    try:
        asyncio.run(scenario())
    finally:
        released.set()
        engine._executor.shutdown(wait=True)


def test_local_generation_limit_eos_sampling_and_context(tmp_path):
    import torch

    engine = LocalCausalLM(tmp_path, "cpu", max_context_tokens=8)
    options = []

    class Tokenizer:
        pad_token_id, eos_token_id = 0, 3

        def apply_chat_template(self, messages, **kwargs):
            return "prompt"

        def __call__(self, prompt, **kwargs):
            return {"input_ids": torch.tensor([[1, 2]])}

        def decode(self, ids, **kwargs):
            return "text"

    class Model:
        config = SimpleNamespace(max_position_embeddings=8)
        eos = False

        def generate(self, **kwargs):
            options.append(kwargs)
            return torch.tensor([[1, 2, 3 if self.eos else 4]])

    engine._model, engine._tokenizer, engine._torch, engine._actual_device = Model(), Tokenizer(), torch, "cpu"
    try:
        assert engine._generate([], None, 1)["finish_reason"] == "length"
        engine._model.eos = True
        assert engine._generate([], None, 1, 0.7, 0.8)["finish_reason"] == "stop"
        assert options[-1]["do_sample"] and options[-1]["temperature"] == 0.7 and options[-1]["top_p"] == 0.8
        with pytest.raises(ContextLengthError):
            engine._generate([], None, 7)
    finally:
        engine._executor.shutdown(wait=True)


def test_thinking_disabled_maps_to_chat_template_kwargs_off_deepseek():
    """Qwen-compatible endpoints get chat_template_kwargs; DeepSeek keeps its own switch."""
    from memoria.llm.backends.openai_http import OpenAICompatBackend
    bailian = ModelConfig(model='qwen3.8-flash', api_key='k',
        base_url='https://llm-17841439ch8g7u4e.cn-beijing.maas.aliyuncs.com/compatible-mode/v1',
        thinking='disabled')
    assert OpenAICompatBackend.provider_options(bailian) == {'chat_template_kwargs': {'enable_thinking': False}}
    deepseek = ModelConfig(model='deepseek-flash', api_key='k',
        base_url='https://api.deepseek.com/v1', thinking='disabled')
    assert OpenAICompatBackend.provider_options(deepseek) == {'thinking': {'type': 'disabled'}}
    unset = ModelConfig(model='qwen3.8-2.4t-a95b', api_key='k',
        base_url='https://llm-17841439ch8g7u4e.cn-beijing.maas.aliyuncs.com/compatible-mode/v1')
    assert OpenAICompatBackend.provider_options(unset) == {}
    enabled = ModelConfig(model='qwen3.8-max', api_key='k',
        base_url='https://llm-17841439ch8g7u4e.cn-beijing.maas.aliyuncs.com/compatible-mode/v1',
        thinking='enabled')
    with pytest.raises(ValueError):
        OpenAICompatBackend.provider_options(enabled)


def test_strict_json_strips_one_markdown_fence_without_relaxing_checks():
    from memoria.memory.reader import strict_json
    assert strict_json('```json\n{"ok": true}\n```') == {'ok': True}
    assert strict_json('```\n{"ok": true}\n```') == {'ok': True}
    assert strict_json('{"ok": true}') == {'ok': True}
    with pytest.raises(ValueError):
        strict_json('```json\n{"a": 1, "a": 2}\n```')  # duplicate keys still rejected
    with pytest.raises(ValueError):
        strict_json('```json\nbroken\n```')  # non-JSON fence content still fails
    with pytest.raises(ValueError):
        strict_json('{"broken"')
