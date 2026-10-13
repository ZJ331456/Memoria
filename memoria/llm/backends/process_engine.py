"""In-process local chat LLM (transformers); optional, loaded lazily like local BGE."""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from functools import lru_cache
from pathlib import Path
from threading import BoundedSemaphore, Event
from typing import Any

from ..types import ContextLengthError, ProviderError

_ROOT = Path(__file__).resolve().parents[3]
_TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL | re.IGNORECASE)
_FUNCTION_RE = re.compile(r"<function=([^>\n]+)>\s*(.*?)</function>", re.DOTALL | re.IGNORECASE)
_PARAM_RE = re.compile(r"<parameter=([^>\n]+)>\s*(.*?)</parameter>", re.DOTALL | re.IGNORECASE)
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


def model_path(model: str) -> Path:
    path = Path(model).expanduser()
    return (path if path.is_absolute() else _ROOT / path).resolve()


def parse_local_completion(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Parse Qwen-style tool calls; strip thinking blocks from visible content."""
    calls: list[dict[str, Any]] = []
    text = _THINK_RE.sub("", text or "")
    text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL | re.IGNORECASE)
    for block in _TOOL_CALL_RE.findall(text):
        for name, body in _FUNCTION_RE.findall(block):
            arguments: dict[str, Any] = {}
            for key, raw in _PARAM_RE.findall(body):
                value = raw.strip()
                try:
                    arguments[key.strip()] = json.loads(value)
                except json.JSONDecodeError:
                    arguments[key.strip()] = value
            calls.append({
                "id": f"local_{uuid.uuid4().hex[:12]}",
                "name": name.strip(),
                "arguments": arguments,
            })
    content = _TOOL_CALL_RE.sub("", text)
    content = re.sub(r"<tool_call>.*$", "", content, flags=re.DOTALL | re.IGNORECASE).strip()
    return content, calls


class VisibleTextFilter:
    """Withhold split thinking/tool tags before text reaches a streaming caller."""

    def __init__(self):
        self.pending = ""
        self.hidden: str | None = None

    def feed(self, text: str, *, final: bool = False) -> str:
        self.pending += text
        output = []
        openings = {"<think>": "</think>", "<tool_call>": "</tool_call>"}
        while self.pending:
            lowered = self.pending.lower()
            if self.hidden:
                index = lowered.find(self.hidden)
                if index < 0:
                    self.pending = self.pending[-(len(self.hidden) - 1):] if not final else ""
                    break
                self.pending = self.pending[index + len(self.hidden):]
                self.hidden = None
                continue
            matches = [(lowered.find(tag), tag) for tag in openings if tag in lowered]
            if matches:
                index, tag = min(matches)
                output.append(self.pending[:index])
                self.pending = self.pending[index + len(tag):]
                self.hidden = openings[tag]
                continue
            keep = 0
            if not final:
                for tag in openings:
                    for length in range(1, len(tag)):
                        if lowered.endswith(tag[:length]):
                            keep = max(keep, length)
            output.append(self.pending[:-keep] if keep else self.pending)
            self.pending = self.pending[-keep:] if keep else ""
            break
        return "".join(output)


class LocalQueueFullError(ProviderError):
    pass


def _arguments_as_mapping(value: Any) -> dict[str, Any]:
    """Qwen chat_template does `arguments|items`; OpenAI wires often keep JSON strings."""
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"_raw": value}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {"value": value}


def _normalize_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(tool_calls, list):
        return normalized
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        clone = dict(call)
        function = clone.get("function")
        if isinstance(function, dict):
            function = dict(function)
            function["arguments"] = _arguments_as_mapping(function.get("arguments"))
            if not function.get("name") and clone.get("name"):
                function["name"] = clone["name"]
            clone["function"] = function
        elif clone.get("name"):
            clone["function"] = {
                "name": clone["name"],
                "arguments": _arguments_as_mapping(clone.get("arguments")),
            }
        normalized.append(clone)
    return normalized


def normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep chat-template-friendly OpenAI-shaped messages."""
    normalized: list[dict[str, Any]] = []
    for item in messages:
        role = str(item.get("role") or "user")
        if role == "developer":
            role = "system"
        entry: dict[str, Any] = {"role": role}
        content = item.get("content")
        if content is not None:
            entry["content"] = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        elif role != "assistant":
            entry["content"] = ""
        if role == "assistant" and item.get("tool_calls"):
            entry["tool_calls"] = _normalize_tool_calls(item.get("tool_calls"))
        if role == "tool":
            if item.get("tool_call_id"):
                entry["tool_call_id"] = item["tool_call_id"]
            if item.get("name"):
                entry["name"] = item["name"]
        normalized.append(entry)
    return normalized


def render_chat_prompt(tokenizer, messages, tools=None):
    options = {"tokenize":False,"add_generation_prompt":True,"enable_thinking":False}
    if tools:
        options["tools"] = tools
    return tokenizer.apply_chat_template(normalize_messages(messages), **options)


class LocalCausalLM:
    """One serial GPU/CPU worker per model+device; shared across LLMClient calls."""

    def __init__(self, path: Path, device: str, max_context_tokens: int = 8192):
        self.path = path
        self.device = device
        self.max_context_tokens = max_context_tokens
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="memoria-llm")
        self._pending = BoundedSemaphore(4)
        self._model: Any = None
        self._tokenizer: Any = None
        self._torch: Any = None
        self._actual_device = ""

    def _load(self) -> None:
        if self._model is not None:
            return
        if not self.path.is_dir():
            raise RuntimeError(f"本地 LLM 模型目录不存在：{self.path}")
        weights = list(self.path.glob("*.safetensors"))
        if not weights and not (self.path / "model.safetensors.index.json").exists():
            raise RuntimeError(f"本地 LLM 权重缺失：{self.path}")
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError("本地 LLM 需要 torch 和 transformers，请使用项目 .venv") from exc
        device = self.device
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA 不可用，请使用已安装 CUDA PyTorch 的 .venv，或选择 local://cpu")
        dtype = torch.bfloat16 if device == "cuda" and torch.cuda.is_bf16_supported() else (
            torch.float16 if device == "cuda" else torch.float32
        )
        tokenizer = AutoTokenizer.from_pretrained(
            str(self.path), local_files_only=True, trust_remote_code=False,
        )
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        model = AutoModelForCausalLM.from_pretrained(
            str(self.path),
            local_files_only=True,
            trust_remote_code=False,
            use_safetensors=True,
            dtype=dtype,
            low_cpu_mem_usage=True,
        )
        model.to(device)
        model.eval()
        self._tokenizer, self._model, self._torch = tokenizer, model, torch
        self._actual_device = device

    def _generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, max_tokens: int,
        temperature: float = 0, top_p: float = 1,
        cancelled: Event | None = None, on_text: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        self._load()
        if cancelled is not None and cancelled.is_set():
            raise RuntimeError("本地 LLM 请求已取消")
        prompt = render_chat_prompt(self._tokenizer, messages, tools)
        encoded = self._tokenizer(prompt, return_tensors="pt")
        encoded = {key: value.to(self._actual_device) for key, value in encoded.items()}
        prompt_len = int(encoded["input_ids"].shape[-1])
        model_limit = getattr(self._model.config, "max_position_embeddings", self.max_context_tokens)
        context_limit = min(self.max_context_tokens, model_limit)
        if prompt_len + max_tokens > context_limit:
            raise ContextLengthError(f"模型上下文超过限制：输入 {prompt_len} + 输出预算 {max_tokens} > {context_limit}")
        pad_id = self._tokenizer.pad_token_id
        eos_id = self._tokenizer.eos_token_id
        from transformers import StoppingCriteria, StoppingCriteriaList, TextStreamer

        class Cancelled(StoppingCriteria):
            def __call__(self, input_ids, scores, **kwargs):
                return cancelled is not None and cancelled.is_set()

        generation: dict[str, Any] = {"do_sample": temperature > 0}
        if temperature > 0:
            generation.update(temperature=temperature, top_p=top_p)
        if on_text:
            visible = VisibleTextFilter()

            class CallbackStreamer(TextStreamer):
                def on_finalized_text(self, text, stream_end=False):
                    safe = visible.feed(text, final=stream_end)
                    if safe:
                        on_text(safe)

            generation["streamer"] = CallbackStreamer(self._tokenizer, skip_prompt=True, skip_special_tokens=True)
        with self._torch.inference_mode():
            output = self._model.generate(
                **encoded,
                max_new_tokens=max_tokens,
                stopping_criteria=StoppingCriteriaList([Cancelled()]),
                pad_token_id=pad_id,
                eos_token_id=eos_id,
                **generation,
            )
        generated = output[0][prompt_len:]
        text = self._tokenizer.decode(generated, skip_special_tokens=True)
        content, calls = parse_local_completion(text)
        reached_eos = bool(generated.numel() and eos_id is not None and int(generated[-1]) == eos_id)
        truncated = int(generated.numel()) >= max_tokens and not reached_eos
        # A length-limited reply must never trigger potentially incomplete actions.
        if truncated:
            calls = []
        raw_calls = [
            {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                },
            }
            for call in calls
        ]
        return {
            "content": content,
            "tool_calls": calls,
            "raw_message": {
                "role": "assistant",
                "content": content or None,
                "tool_calls": raw_calls,
            },
            "usage": {
                "prompt_tokens": prompt_len,
                "completion_tokens": int(generated.numel()),
                "total_tokens": prompt_len + int(generated.numel()),
            },
            "finish_reason": "length" if truncated else ("tool_calls" if calls else "stop"),
            "device": self._actual_device,
        }

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        timeout: float,
        *,
        temperature: float = 0,
        top_p: float = 1,
        on_text: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if not self._pending.acquire(blocking=False):
            raise LocalQueueFullError("本地 LLM 队列已满，请稍后重试")
        cancelled = Event()
        try:
            future = self._executor.submit(self._generate, messages, tools, max_tokens, temperature, top_p, cancelled, on_text)
        except BaseException:
            self._pending.release()
            raise
        future.add_done_callback(lambda _: self._pending.release())
        try:
            return await asyncio.wait_for(asyncio.wrap_future(future), timeout=timeout)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            cancelled.set()
            raise

    async def stream_chat(self, messages, tools, max_tokens, timeout, **options):
        """Text deltas are live; tool rounds are buffered until safely parsed."""
        if tools:
            yield {"result": await self.chat(messages, tools, max_tokens, timeout, **options)}
            return
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def on_text(text):
            loop.call_soon_threadsafe(queue.put_nowait, text)

        task = asyncio.create_task(self.chat(messages, tools, max_tokens, timeout, on_text=on_text, **options))
        task.add_done_callback(lambda _: queue.put_nowait(None))
        try:
            while True:
                text = await queue.get()
                if text is None:
                    break
                yield {"delta": text}
            yield {"result": await task}
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    def public_status(self) -> dict[str, Any]:
        return {
            "device": self._actual_device or self.device,
            "path": str(self.path),
            "loaded": self._model is not None,
        }


@lru_cache(maxsize=2)
def get_local_llm(path: str, device: str, max_context_tokens: int = 8192) -> LocalCausalLM:
    return LocalCausalLM(Path(path), device, max_context_tokens)
