"""Budget the entire evidence envelope, not the complete reader conversation."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
import json


def serialize(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass
class EvidenceBudget:
    limit: int
    unit: str = "chars"
    tokenizer: Callable[[str], list] | None = None

    def __post_init__(self):
        if self.limit < 2 or self.unit not in {"chars", "utf8_bytes", "reader_tokens"}:
            raise ValueError("invalid evidence budget")
        if self.unit == "reader_tokens" and self.tokenizer is None:
            raise ValueError("reader_tokens requires the actual reader tokenizer; no embedding fallback")

    def cost(self, payload):
        text = serialize(payload)
        if self.unit == "reader_tokens":
            return len(self.tokenizer(text))
        return len(text.encode("utf-8")) if self.unit == "utf8_bytes" else len(text)

    def public(self):
        return {"limit": self.limit, "unit": self.unit, "exact_reader_tokens": self.unit == "reader_tokens",
                "scope": "complete_evidence_json; excludes system/query/tools/output",
                "interpretation": "UTF-8 bytes are a byte budget, not measured remote-model tokens"}
