from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ProviderError(RuntimeError):
    pass


class ContextLengthError(ProviderError):
    pass


class ContentSafetyError(ProviderError):
    pass


class IncompleteResponseError(ValueError):
    """A recorded provider completion that must never be parsed as a final answer."""
    def __init__(self, finish_reason):
        self.finish_reason = finish_reason
        super().__init__(f'incomplete final content: {finish_reason}')


@dataclass(slots=True)
class ChatResult:
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw_message: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0
    retries: int = 0
    finish_reason: str | None = None
    budget: dict[str, Any] = field(default_factory=dict)
