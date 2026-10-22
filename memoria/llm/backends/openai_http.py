"""Unified OpenAI-compatible HTTP backend.

Works for remote APIs (DeepSeek / DashScope) and local servers
(vLLM / SGLang / Ollama / llama.cpp / Memoria serve_local_llm).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from ...config import ModelConfig
from ..types import ChatResult, ContentSafetyError, ContextLengthError, ProviderError
from ..tls import async_verified_context

logger = logging.getLogger(__name__)
_CONTEXT_HINTS = (
    "context_length_exceeded", "maximum context length", "context window",
    "range of input length", "too many tokens",
)
_SAFETY_HINTS = ("content_filter", "content_policy_violation", "data_inspection_failed")


def classify_openai_server(base_url: str) -> str:
    """Best-effort label for docs/status; routing always uses the same HTTP path."""
    host = (urlparse(base_url).hostname or "").lower()
    path = (urlparse(base_url).path or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost"):
        if ":11434" in base_url:
            return "ollama"
        if "sglang" in path or ":30000" in base_url:
            return "sglang"
        if ":8000" in base_url or "vllm" in path:
            return "vllm-or-openai-local"
        return "openai-local"
    if "deepseek" in host:
        return "deepseek"
    if "dashscope" in host:
        return "dashscope"
    return "openai-compat"


class OpenAICompatBackend:
    name = "openai_http"

    @classmethod
    def payload(cls, selected, messages, max_tokens, tools=None, stream=False, temperature=None, top_p=None):
        payload = {"model":selected.model,"messages":messages,"max_tokens":max_tokens,"stream":stream}
        if stream:
            payload["stream_options"] = {"include_usage":True}
        if tools:
            payload.update(tools=tools,tool_choice="auto")
        payload.update({k:v for k,v in {"temperature":temperature,"top_p":top_p}.items() if v is not None})
        payload.update(cls.provider_options(selected))
        return payload

    @staticmethod
    def provider_options(selected):
        if selected.thinking is None:
            return {}
        url = urlparse(selected.base_url)
        if url.scheme != "https" or url.hostname != "api.deepseek.com":
            raise ValueError("Explicit thinking switch is supported only for the official DeepSeek API")
        return {"thinking": {"type": selected.thinking}}

    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_retries: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.transport = transport

    async def chat(
        self,
        selected: ModelConfig,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
        stream: bool = False,
        temperature: float | None = None, top_p: float | None = None,
    ) -> ChatResult:
        if stream:
            return await self._chat_stream(selected, messages, max_tokens=max_tokens, tools=tools, on_delta=on_delta, temperature=temperature, top_p=top_p)
        payload = self.payload(selected, messages, max_tokens, tools, False, temperature, top_p)
        data, metrics = await self._post(selected, selected.auth_headers(), payload)
        try:
            choice = data["choices"][0]
            if not isinstance(choice, dict):
                raise ValueError("choice must be an object")
            message = choice["message"]
            if not isinstance(message, dict):
                raise ValueError("message must be an object")
            calls = []
            for call in (message.get("tool_calls") or []) if choice.get("finish_reason") != "length" else []:
                fn = call["function"]
                arguments = self._tool_arguments(fn.get("arguments"))
                calls.append({"id": call["id"], "name": fn["name"], "arguments": arguments})
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("模型返回了无效的 Chat Completions 响应") from exc
        if choice.get("finish_reason") == "content_filter":
            raise ContentSafetyError("模型供应商拒绝了不安全内容")
        usage = {key: int(value) for key, value in (data.get("usage") or {}).items() if type(value) is int}
        return ChatResult(
            str(message.get("content") or ""),
            calls,
            message,
            usage,
            metrics["duration_ms"],
            metrics["retries"],
            choice.get("finish_reason"),
        )

    async def _post(
        self,
        selected: ModelConfig,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, int]]:
        last_error: Exception | None = None
        started = time.perf_counter()
        timeout = httpx.Timeout(self.timeout_seconds)
        verify = True if self.transport is not None else await async_verified_context(not selected.is_local_http())
        async with httpx.AsyncClient(timeout=timeout, transport=self.transport, trust_env=not selected.is_local_http(), verify=verify) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    response = await client.post(
                        f"{selected.base_url.rstrip('/')}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                    if response.status_code >= 400:
                        self._raise_response_error(response)
                    try:
                        data = response.json()
                        if not isinstance(data, dict):
                            raise ValueError("response must be an object")
                    except ValueError as exc:
                        raise ProviderError("模型返回了无效的 JSON 响应") from exc
                    return data, {
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                        "retries": attempt,
                    }
                except (ContextLengthError, ContentSafetyError):
                    raise
                except (httpx.TimeoutException, httpx.TransportError, ProviderError) as exc:
                    last_error = exc
                    if not self._retryable(exc) or attempt >= self.max_retries:
                        if isinstance(exc, ProviderError):
                            raise
                        raise ProviderError(f"模型服务连接失败: {type(exc).__name__}: {exc}") from exc
                    delay = min(8.0, float(2**attempt))
                    logger.warning(
                        "LLM 请求失败，%.1f 秒后重试 attempt=%d/%d model=%s server=%s error=%s",
                        delay, attempt + 1, self.max_retries + 1, selected.model,
                        classify_openai_server(selected.base_url), type(exc).__name__,
                    )
                    await asyncio.sleep(delay)
        raise ProviderError(str(last_error or "模型请求失败"))

    async def _chat_stream(
        self,
        selected: ModelConfig,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        tools: list[dict[str, Any]] | None,
        on_delta: Callable[[str], Awaitable[None]] | None,
        temperature: float | None = None, top_p: float | None = None,
    ) -> ChatResult:
        payload = self.payload(selected, messages, max_tokens, tools, True, temperature, top_p)
        headers = selected.auth_headers()
        started = time.perf_counter()
        last_error: Exception | None = None
        attempt = 0
        while attempt <= self.max_retries:
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            call_parts: dict[int, dict[str, Any]] = {}
            usage: dict[str, int] = {}
            emitted = False
            done = False
            saw_choice = False
            finish_reason = None
            try:
                verify = True if self.transport is not None else await async_verified_context(not selected.is_local_http())
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout_seconds),
                    transport=self.transport,
                    trust_env=not selected.is_local_http(),
                    verify=verify,
                ) as client:
                    async with client.stream(
                        "POST",
                        f"{selected.base_url.rstrip('/')}/chat/completions",
                        headers=headers,
                        json=payload,
                    ) as response:
                        if response.status_code >= 400:
                            await response.aread()
                            lowered = response.text.lower()
                            if (
                                "stream_options" in payload
                                and response.status_code in {400, 422}
                                and ("stream_options" in lowered or "unknown" in lowered or "unsupported" in lowered)
                            ):
                                payload.pop("stream_options", None)
                                continue
                            self._raise_response_error(response)
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if raw == "[DONE]":
                                done = True
                                break
                            if not raw:
                                continue
                            try:
                                event = json.loads(raw)
                            except json.JSONDecodeError:
                                raise ProviderError("模型流返回了无效的 JSON") from None
                            if not isinstance(event, dict):
                                raise ProviderError("模型流事件必须是 JSON 对象")
                            if event.get("error"):
                                raise ProviderError(f"模型流返回错误: {str(event['error'])[:300]}")
                            if event.get("usage"):
                                usage = {
                                    key: int(value)
                                    for key, value in event["usage"].items()
                                    if isinstance(value, int) and not isinstance(value, bool)
                                }
                            choices = event.get("choices") or []
                            if not choices:
                                continue
                            saw_choice = True
                            if choices[0].get("finish_reason"):
                                finish_reason = choices[0]["finish_reason"]
                            if finish_reason == "content_filter":
                                raise ContentSafetyError("模型供应商拒绝了不安全内容")
                            delta = choices[0].get("delta") or {}
                            text = str(delta.get("content") or "")
                            if text:
                                emitted = True
                                content_parts.append(text)
                                if on_delta:
                                    await on_delta(text)
                            reasoning = str(delta.get("reasoning_content") or "")
                            if reasoning:
                                emitted = True
                                reasoning_parts.append(reasoning)
                            for fragment in delta.get("tool_calls") or []:
                                emitted = True
                                index = int(fragment.get("index", 0))
                                target = call_parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
                                if fragment.get("id"):
                                    target["id"] = fragment["id"]
                                function = fragment.get("function") or {}
                                if function.get("name"):
                                    target["name"] += function["name"]
                                target["arguments"] += str(function.get("arguments") or "")
                if not saw_choice or (not done and finish_reason is None):
                    raise ProviderError("模型流在完成标记之前中断")
                calls, raw_calls = [], []
                for _, item in sorted(call_parts.items()):
                    if finish_reason != "length":
                        arguments = self._tool_arguments(item["arguments"])
                        calls.append({"id": item["id"], "name": item["name"], "arguments": arguments})
                    raw_calls.append({
                        "id": item["id"],
                        "type": "function",
                        "function": {"name": item["name"], "arguments": item["arguments"] or "{}"},
                    })
                content = "".join(content_parts)
                raw_message = {"role": "assistant", "content": content or None, "tool_calls": raw_calls}
                if reasoning_parts:
                    raw_message["reasoning_content"] = "".join(reasoning_parts)
                return ChatResult(content, calls, raw_message, usage, int((time.perf_counter() - started) * 1000), attempt, finish_reason)
            except (ContextLengthError, ContentSafetyError, asyncio.CancelledError):
                raise
            except (httpx.TimeoutException, httpx.TransportError, ProviderError) as exc:
                last_error = exc
                if emitted:
                    raise ProviderError("流式响应已输出部分内容后中断，为避免重复内容未自动重试") from exc
                if not self._retryable(exc) or attempt >= self.max_retries:
                    if isinstance(exc, ProviderError):
                        raise
                    raise ProviderError(f"模型流连接失败: {type(exc).__name__}: {exc}") from exc
                await asyncio.sleep(min(8.0, float(2**attempt)))
                attempt += 1
        raise ProviderError(str(last_error or "模型流请求失败"))

    @staticmethod
    def _tool_arguments(value: Any) -> dict[str, Any]:
        try:
            arguments = json.loads(value or "{}") if isinstance(value, str) or value is None else value
        except (TypeError, ValueError) as exc:
            raise ProviderError("模型工具参数不是有效的 JSON 对象") from exc
        if not isinstance(arguments, dict):
            raise ProviderError("模型工具参数必须是 JSON 对象")
        return arguments

    @staticmethod
    def _raise_response_error(response: httpx.Response) -> None:
        text = response.text[:2000]
        lowered = text.lower()
        if any(hint in lowered for hint in _SAFETY_HINTS):
            raise ContentSafetyError("模型供应商拒绝了不安全内容")
        if any(hint in lowered for hint in _CONTEXT_HINTS):
            raise ContextLengthError("模型上下文超过限制")
        raise ProviderError(f"模型服务返回 HTTP {response.status_code}: {text[:300]}")

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return True
        text = str(exc).lower()
        return any(
            token in text
            for token in (
                "http 429", "http 500", "http 502", "http 503", "http 504",
                "rate limit", "temporarily unavailable",
            )
        )
