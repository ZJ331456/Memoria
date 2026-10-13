"""Text chat and BGE served through a bounded OpenAI-compatible HTTP subset."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import struct
import time
import uuid
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .backends.process_engine import LocalQueueFullError, get_local_llm, model_path
from .types import ContextLengthError

logger = logging.getLogger(__name__)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None

    @model_validator(mode="after")
    def text_only(self):
        if isinstance(self.content, list):
            if any(part.get("type") != "text" or not isinstance(part.get("text"), str) for part in self.content):
                raise ValueError("此本地后端仅支持文本消息；图片/音频请使用对应多模态服务")
            self.content = "".join(part["text"] for part in self.content)
        if self.content is None and not (self.role == "assistant" and self.tool_calls):
            raise ValueError("content is required except for assistant tool calls")
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("tool_call_id is required for tool messages")
        if self.tool_calls and self.role != "assistant":
            raise ValueError("tool_calls is supported only on assistant messages")
        for call in self.tool_calls or []:
            function = call.get("function")
            if call.get("type") != "function" or not call.get("id") or not isinstance(function, dict):
                raise ValueError("tool_calls must contain function/id/type")
            if not isinstance(function.get("name"), str) or not function["name"]:
                raise ValueError("function name is required")
            try:
                arguments = json.loads(function.get("arguments", "{}"))
            except (TypeError, ValueError):
                raise ValueError("function arguments must be a JSON object string") from None
            if not isinstance(arguments, dict):
                raise ValueError("function arguments must be a JSON object string")
        return self


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: str = Field(min_length=1)
    messages: list[ChatMessage] = Field(min_length=1, max_length=512)
    max_tokens: int | None = Field(default=None, ge=1, le=8192)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=8192)
    temperature: float = Field(default=0, ge=0, le=2)
    top_p: float = Field(default=1, gt=0, le=1)
    stream: bool = False
    stream_options: dict[str, bool] | None = None
    tools: list[dict[str, Any]] | None = Field(default=None, max_length=64)
    tool_choice: Literal["auto", "none"] = "auto"
    n: Literal[1] = 1

    @model_validator(mode="after")
    def supported_options(self):
        if self.max_tokens is not None and self.max_completion_tokens is not None:
            raise ValueError("use either max_tokens or max_completion_tokens")
        if self.stream_options and (not self.stream or set(self.stream_options) != {"include_usage"}):
            raise ValueError("stream_options supports include_usage only, with stream=true")
        for tool in self.tools or []:
            function = tool.get("function")
            if tool.get("type") != "function" or not isinstance(function, dict) or not isinstance(function.get("name"), str) or not function["name"]:
                raise ValueError("tools must contain named function definitions")
            if not isinstance(function.get("parameters", {}), dict) or function.get("strict"):
                raise ValueError("tools require a parameters object; strict schema decoding is unsupported")
        return self


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: str = Field(min_length=1)
    input: str | list[str]
    encoding_format: Literal["float", "base64"] = "float"
    dimensions: Literal[512] | None = None

    @field_validator("input")
    @classmethod
    def bounded_text(cls, value):
        texts = [value] if isinstance(value, str) else value
        if not texts or len(texts) > 64 or any(not text or len(text) > 32000 for text in texts):
            raise ValueError("input requires 1..64 non-empty strings, each at most 32000 characters")
        return value


def _error(message: str, *, code="invalid_request_error", param=None, error_type="invalid_request_error"):
    return {"error": {"message": message, "type": error_type, "param": param, "code": code}}


def _inference_error(exc: Exception) -> tuple[int, dict]:
    from ..memory.local_bge import EmbeddingInputTooLong
    if isinstance(exc, EmbeddingInputTooLong):
        return 400, _error(str(exc), code="context_length_exceeded", param="input")
    if isinstance(exc, ContextLengthError):
        return 400, _error(str(exc), code="context_length_exceeded", param="messages")
    if isinstance(exc, LocalQueueFullError) or (isinstance(exc, RuntimeError) and "队列已满" in str(exc)):
        return 429, _error(str(exc), code="rate_limit_exceeded", error_type="rate_limit_error")
    if isinstance(exc, asyncio.TimeoutError):
        return 504, _error("本地推理超时", code="timeout", error_type="server_error")
    logger.error("本地推理失败", exc_info=exc)
    return 500, _error("本地模型推理失败，请检查服务日志与模型环境", code="inference_error", error_type="server_error")


def create_local_openai_app(
    *, model: str = "model/Qwen3.5-2B", device: str = "cuda",
    served_model_name: str | None = None, embedding_model: str | None = None,
    max_context_tokens: int = 8192, request_timeout: float = 300,
) -> FastAPI:
    if device not in {"cuda", "cpu", "auto"} or max_context_tokens < 2 or request_timeout <= 0:
        raise ValueError("invalid device/context/timeout")
    app = FastAPI(title="Memoria Local OpenAI Server", version="0.2.0")
    model_id = served_model_name or model
    engine = get_local_llm(str(model_path(model)), device, max_context_tokens)
    embedding_id = "bge-small-zh-v1.5" if embedding_model else None
    embedding_encoder = None

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc):
        detail = exc.detail if isinstance(exc.detail, dict) else _error(str(exc.detail))
        return JSONResponse(status_code=exc.status_code, content=detail)

    async def parse(request, schema):
        try:
            return schema.model_validate(await request.json())
        except ValidationError as exc:
            first = exc.errors(include_input=False)[0]
            param = ".".join(map(str, first["loc"])) or None
            raise HTTPException(400, _error(first["msg"], param=param)) from None
        except (ValueError, TypeError):
            raise HTTPException(400, _error("request body must be a JSON object")) from None

    def require_model(requested, aliases):
        if requested not in aliases:
            raise HTTPException(404, _error(f"模型不存在：{requested}", code="model_not_found", param="model"))

    @app.get("/health")
    async def health():
        return {"ok": True, "backend": "process_local", "device": device, "model": model_id,
                "loaded": engine.public_status()["loaded"], "max_context_tokens": max_context_tokens}

    @app.get("/v1/models")
    async def list_models():
        ids = [model_id] + ([embedding_id] if embedding_id else [])
        return {"object": "list", "data": [{"id": value, "object": "model", "created": 0,
                                            "owned_by": "memoria-local"} for value in ids]}

    @app.post("/v1/chat/completions")
    @app.post("/v1/reader-token-count")
    async def chat_completions(request: Request):
        body = await parse(request, ChatRequest)
        require_model(body.model, {model_id, model, model_path(model).name})
        messages = [message.model_dump(exclude_none=True) for message in body.messages]
        tools = body.tools if body.tool_choice == "auto" else None
        limit = body.max_tokens or body.max_completion_tokens or 256
        options = {"temperature": body.temperature, "top_p": body.top_p}
        if request.url.path.endswith('/reader-token-count'):
            from ..prompting.reader_budget import count_local_path
            try:
                count,window,fingerprint=await asyncio.to_thread(count_local_path,model_path(model),messages,tools,max_context_tokens)
            except Exception as exc:
                raise HTTPException(503,_error('本地tokenizer计数不可用',code='tokenizer_unavailable')) from exc
            return {'protocol':'memoria-chat-template-v1','model':body.model,'input_tokens':count,
                    'context_tokens':window,'tokenizer_sha256':fingerprint}
        created = int(time.time())
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"

        def chunk(delta, finish=None, *, choices=True, usage=None):
            value = {"id": completion_id, "object": "chat.completion.chunk", "created": created,
                     "model": model_id, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}] if choices else []}
            if usage is not None:
                value["usage"] = usage
            return f"data: {json.dumps(value, ensure_ascii=False)}\n\n"

        if not body.stream:
            try:
                data = await engine.chat(messages, tools, limit, timeout=request_timeout, **options)
            except Exception as exc:
                status, error = _inference_error(exc)
                raise HTTPException(status, error) from exc
            return {"id": completion_id, "object": "chat.completion", "created": created,
                    "model": model_id, "choices": [{"index": 0, "message": data["raw_message"],
                                                    "finish_reason": data["finish_reason"]}], "usage": data["usage"]}

        async def event_stream():
            streamed = False
            yield chunk({"role": "assistant"})
            try:
                async for event in engine.stream_chat(messages, tools, limit, timeout=request_timeout, **options):
                    if "delta" in event:
                        streamed = True
                        yield chunk({"content": event["delta"]})
                    else:
                        data = event["result"]
                        if not streamed and data.get("content"):
                            yield chunk({"content": data["content"]})
                        raw_calls = data["raw_message"].get("tool_calls") or []
                        if raw_calls:
                            yield chunk({"tool_calls": [{"index": index, **call} for index, call in enumerate(raw_calls)]})
                        yield chunk({}, data["finish_reason"])
                        if body.stream_options and body.stream_options.get("include_usage"):
                            yield chunk({}, choices=False, usage=data["usage"])
            except Exception as exc:
                _, error = _inference_error(exc)
                yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/v1/embeddings")
    async def embeddings(request: Request):
        nonlocal embedding_encoder
        body = await parse(request, EmbeddingRequest)
        require_model(body.model, {embedding_id, embedding_model} if embedding_model else set())
        from ..memory.local_bge import get_encoder, model_fingerprint, model_path as bge_path
        texts = [body.input] if isinstance(body.input, str) else body.input
        path = bge_path(embedding_model)
        try:
            # Freeze this deployment's model identity; do not hash 96 MB on every query.
            if embedding_encoder is None:
                embedding_encoder = get_encoder(str(path), device, "embedding-local:" + model_fingerprint(path))
            encoder = embedding_encoder
            vectors = await encoder.encode(texts, request_timeout)
            tokens = await asyncio.to_thread(encoder.count_tokens, texts)
        except Exception as exc:
            status, error = _inference_error(exc)
            raise HTTPException(status, error) from exc
        data = []
        for index, vector in enumerate(vectors):
            value = vector if body.encoding_format == "float" else base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode("ascii")
            data.append({"object": "embedding", "index": index, "embedding": value})
        return {"object": "list", "data": data, "model": embedding_id,
                "usage": {"prompt_tokens": tokens, "total_tokens": tokens}}

    return app
