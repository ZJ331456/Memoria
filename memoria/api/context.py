"""Explicit tenant composition shared by route groups; never selected from request bodies."""

from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI
    from ..config import Settings
    from ..store import Store
    from ..service import AgentService
    from ..governance import MemoryGovernance
    from ..observability import MetricRegistry
    from ..security import RequestGate


@dataclass(frozen=True)
class TenantApiContext:
    app: FastAPI
    settings: Settings
    store: Store | None
    service: AgentService | None
    governance: MemoryGovernance | None
    metrics: MetricRegistry
    session_locks: dict[str, asyncio.Lock]
    active_turns: dict[str, asyncio.Task]
    deleting_sessions: set[str]
    request_gate: RequestGate
