from __future__ import annotations

import asyncio
import logging
import hashlib
import math
from functools import cached_property
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import ModelConfig

logger = logging.getLogger(__name__)


@dataclass
class DocumentEmbedding:
    vector: list[float]
    chunks: list[dict[str, Any]] | None = None


class EmbeddingError(RuntimeError):
    """The configured embedding provider could not produce usable vectors."""


class EmbeddingClient:
    """Remote OpenAI embeddings or optional, offline local BGE inference."""

    def __init__(self, config: ModelConfig, timeout_seconds: float = 30, max_retries: int = 2, batch_size: int = 10):
        config.validate_role("embedding")
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self.batch_size = max(1, min(batch_size, 64))

    @property
    def is_local(self) -> bool:
        return self.config.base_url.startswith("local://")

    @property
    def uses_bge_instruction(self) -> bool:
        name = self.config.model.replace("\\", "/").rstrip("/").split("/")[-1]
        return self.is_local or (self.config.is_local_http() and name == "bge-small-zh-v1.5")

    @cached_property
    def namespace(self) -> str:
        if self.is_local:
            from .local_bge import model_fingerprint, model_path
            return "embedding-local:" + model_fingerprint(model_path(self.config.model))
        # Provider endpoint and model identify the vector space. Keys are excluded.
        identity = f"{self.config.base_url.rstrip('/')}|{self.config.model}"
        if self.uses_bge_instruction:
            from .local_bge import POLICY
            identity += "|" + POLICY
        return "embedding:" + hashlib.sha256(identity.encode()).hexdigest()

    @property
    def enabled(self) -> bool:
        return self.config.is_ready(role="embedding")

    @property
    def supports_document_chunks(self) -> bool:
        return self.uses_bge_instruction

    async def embed_documents(self, texts: list[str], *, timings=None) -> list[DocumentEmbedding]:
        """Encode every source slice. Parent vectors are only coarse summaries;
        retrieval uses the best matching source chunk rather than its average.
        """
        from ..observability.timing import StageTimings
        from .token_chunks import local_chunker
        timers = timings or StageTimings()
        if not self.supports_document_chunks:
            with timers.measure("embedding.documents"):
                vectors = await self.embed_batch(texts)
            return [DocumentEmbedding(v) for v in vectors]
        with timers.measure("embedding.chunking"):
            tokenizer_model = self.config.model if self.is_local else "model/bge-small-zh-v1.5"
            chunker = local_chunker(tokenizer_model)
            documents = await asyncio.to_thread(lambda: [chunker.split(text) for text in texts])
        with timers.measure("embedding.documents"):
            vectors = await self.embed_batch([c.content for doc in documents for c in doc])
        if len(vectors) != sum(map(len, documents)):
            raise EmbeddingError("document chunk encoding is incomplete")
        result, offset = [], 0
        for doc in documents:
            group = vectors[offset:offset + len(doc)]
            offset += len(doc)
            if len({len(v) for v in group}) != 1:
                raise EmbeddingError("document chunks have inconsistent vector dimensions")
            mean = [sum(values) / len(group) for values in zip(*group, strict=True)]
            norm = math.sqrt(sum(x * x for x in mean))
            summary = [x / norm for x in mean] if norm else group[0]
            result.append(DocumentEmbedding(summary, [
                {"start": c.start, "end": c.end, "tokens": c.tokens, "embedding": v}
                for c, v in zip(doc, group, strict=True)]))
        return result

    async def warmup(self) -> None:
        """Load local weights before serving; remote providers are never called."""
        if self.is_local and self.enabled:
            startup_client = EmbeddingClient(self.config, max(120, self.timeout_seconds), max_retries=0)
            await startup_client.embed("本地记忆模型预热")

    async def embed_query(self, text: str) -> list[float] | None:
        if self.uses_bge_instruction:
            from .local_bge import QUERY_INSTRUCTION, EN_QUERY_INSTRUCTION
            name = self.config.model.replace('\\', '/').rstrip('/').split('/')[-1]
            text = (EN_QUERY_INSTRUCTION if name == 'bge-small-en-v1.5' else QUERY_INSTRUCTION) + text
        return await self.embed(text)

    async def embed(self, text: str) -> list[float] | None:
        vectors = await self.embed_batch([text])
        return vectors[0] if vectors else None

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts or not self.enabled:
            return []
        result: list[list[float]] = []
        for offset in range(0, len(texts), self.batch_size):
            chunk = texts[offset:offset + self.batch_size]
            result.extend(await self._request(chunk))
        return result

    async def _request(self, texts: list[str]) -> list[list[float]]:
        if self.is_local:
            from .local_bge import get_encoder, model_path
            device = self.config.base_url.removeprefix("local://").rstrip("/")
            if device not in {"cuda", "cpu", "auto"}:
                raise EmbeddingError("本地 Embedding 地址应为 local://cuda、local://cpu 或 local://auto")
            try:
                encoder = get_encoder(str(model_path(self.config.model)), device, self.namespace)
                vectors = await encoder.encode(texts, self.timeout_seconds)
                return self._vectors({"data": [{"index": i, "embedding": vector}
                    for i, vector in enumerate(vectors)]}, len(texts))
            except (RuntimeError, ValueError, OSError, ImportError, asyncio.TimeoutError) as exc:
                raise EmbeddingError(f"本地 Embedding 失败：{exc}") from exc
        headers = self.config.auth_headers()
        payload = {"model": self.config.model, "input": texts, "encoding_format": "float"}
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds), trust_env=not self.config.is_local_http()) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    response = await client.post(f"{self.config.base_url.rstrip('/')}/embeddings", headers=headers, json=payload)
                    if response.status_code >= 400:
                        raise EmbeddingError(f"embedding 服务返回 HTTP {response.status_code}: {response.text[:300]}")
                    return self._vectors(response.json(), len(texts))
                except (httpx.TimeoutException, httpx.TransportError, EmbeddingError, KeyError, TypeError, ValueError) as exc:
                    last_error = exc
                    retryable = isinstance(exc, (httpx.TimeoutException, httpx.TransportError)) or any(
                        token in str(exc).lower() for token in ("http 429", "http 500", "http 502", "http 503", "http 504")
                    )
                    if not retryable or attempt >= self.max_retries:
                        break
                    await asyncio.sleep(min(4.0, float(2**attempt)))
        raise EmbeddingError(f"embedding 请求失败: {type(last_error).__name__}: {last_error}") from last_error

    @staticmethod
    def _vectors(payload: dict[str, Any], expected: int) -> list[list[float]]:
        data = sorted(payload["data"], key=lambda item: int(item.get("index", 0)))
        if any("index" in item for item in data) and [int(item.get("index", -1)) for item in data] != list(range(expected)):
            raise EmbeddingError("embedding 响应序号缺失或重复")
        vectors = [[float(value) for value in item["embedding"]] for item in data]
        if len(vectors) != expected or any(not vector for vector in vectors):
            raise EmbeddingError(f"embedding 响应数量异常，期望 {expected}，实际 {len(vectors)}")
        dimension = len(vectors[0])
        if any(len(vector) != dimension for vector in vectors):
            raise EmbeddingError("embedding 响应向量维度不一致")
        if any(not any(vector) or any(not math.isfinite(value) for value in vector) for vector in vectors):
            raise EmbeddingError("embedding 响应包含非有限数值或零向量")
        return vectors
