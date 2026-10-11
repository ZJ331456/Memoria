"""Lossless source slices with a token budget that includes repeated headers.

This prepares retrieval documents, not pooled vectors. No model weights or network
are needed; callers choose the same tokenizer/protocol for all compared methods.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

PROTOCOL = "source-token-chunks-v1:no-overlap:header-inclusive"


@dataclass(frozen=True)
class TokenChunk:
    content: str
    start: int
    end: int
    tokens: int


class TokenChunker:
    def __init__(self, tokenizer: Any, max_tokens: int = 512, max_chars: int = 1600):
        if max_tokens < 3 or max_chars < 1:
            raise ValueError("invalid chunk token/character budget")
        self.tokenizer = tokenizer
        self.max_tokens = max_tokens
        self.max_chars = max_chars

    def count(self, text: str) -> int:
        # Includes CLS/SEP and does not truncate. Do not confuse padded batch
        # length with the tokens actually consumed by an individual document.
        return len(self.tokenizer(text, truncation=False, add_special_tokens=True)["input_ids"])

    def split(self, text: str, prefix: Callable[[int], str] = lambda part: "") -> list[TokenChunk]:
        chunks: list[TokenChunk] = []
        start = 0
        while start < len(text) or not chunks:
            header = prefix(len(chunks))
            if self.count(header) > self.max_tokens:
                raise ValueError("source header exceeds the embedding token budget")
            end = min(len(text), start + self.max_chars)
            if self.count(header + text[start:end]) > self.max_tokens:
                # Find a conservative fitting source slice. Re-tokenize header
                # + body together because token counts need not be additive.
                low, high = start, end
                while low < high:
                    middle = (low + high + 1) // 2
                    if self.count(header + text[start:middle]) <= self.max_tokens:
                        low = middle
                    else:
                        high = middle - 1
                end = low
            if end == start and start < len(text):
                raise ValueError("source header leaves no room for the next character")
            content = header + text[start:end]
            tokens = self.count(content)
            if tokens > self.max_tokens:
                raise ValueError("chunk exceeds embedding token budget")
            chunks.append(TokenChunk(content, start, end, tokens))
            start = end
        return chunks

    def coverage(self, text: str, chunks: list[TokenChunk]) -> dict[str, Any]:
        cursor = 0
        for chunk in chunks:
            if chunk.start != cursor or not chunk.start <= chunk.end <= len(text):
                raise ValueError("source chunks contain a gap or overlap")
            cursor = chunk.end
        if cursor != len(text):
            raise ValueError("source chunks do not cover the complete input")
        return {"source_chars": len(text), "covered_chars": cursor,
                "source_tokens": self.count(text), "chunks": len(chunks),
                "max_encoded_tokens": max((c.tokens for c in chunks), default=0),
                "truncated_chunks": 0,
                "spans": [{"start": c.start, "end": c.end, "tokens": c.tokens} for c in chunks]}


def tokenizer_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    for name in ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "vocab.txt", "added_tokens.json"):
        file = path / name
        if file.is_file():
            digest.update(name.encode())
            digest.update(file.read_bytes())
    return digest.hexdigest()


@lru_cache(maxsize=4)
def _load_tokenizer(path: str, fingerprint: str):
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
    # The chunker deliberately inspects untruncated sequences longer than the
    # encoder limit. Avoid transformers' misleading model-forward warning.
    tokenizer.model_max_length = 10**12
    return tokenizer


def local_chunker(model: str, *, max_chars: int = 1600) -> TokenChunker:
    from .local_bge import MAX_LENGTH, model_path
    path = model_path(model)
    if not (path / "tokenizer_config.json").is_file():
        raise ValueError(f"local tokenizer is missing: {path}")
    return TokenChunker(_load_tokenizer(str(path), tokenizer_fingerprint(path)), MAX_LENGTH, max_chars)


def chunking_metadata(model: str | None, max_chars: int = 1600) -> dict[str, Any]:
    if not model:
        return {"protocol": "source-character-chunks-v1", "max_chars": max_chars,
                "token_limit_verified": False}
    from .local_bge import MAX_LENGTH, model_path
    return {"protocol": PROTOCOL, "tokenizer": Path(model).name,
            "tokenizer_sha256": tokenizer_fingerprint(model_path(model)),
            "max_tokens": MAX_LENGTH, "max_chars": max_chars,
            "special_tokens_and_headers_included": True, "token_limit_verified": True}
