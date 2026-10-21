"""Stable HTTP data contracts, independent of route construction."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from ..claims import ClaimDraft

MemoryKind = Literal["fact", "preference", "profile", "goal", "procedure"]


class StrictModel(BaseModel):
    """Public request bodies reject unknown fields instead of silently ignoring them."""

    model_config = ConfigDict(extra="forbid")


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str


class SessionBody(StrictModel):
    title: str = Field(default="新对话", min_length=1, max_length=80, examples=["Rust 学习计划"])


class SessionPatch(StrictModel):
    title: str = Field(min_length=1, max_length=80)


class SessionResponse(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0


class MessageResponse(BaseModel):
    id: str
    session_id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: str


class ChatBody(StrictModel):
    content: str = Field(min_length=1, max_length=20000, examples=["请记住我偏好简洁回答"])


class MemoryBody(StrictModel):
    content: str = Field(min_length=1, max_length=4000)
    kind: MemoryKind = "fact"
    importance: int = Field(default=3, ge=1, le=5)


class MemoryPatch(StrictModel):
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    kind: MemoryKind | None = None
    importance: int | None = Field(default=None, ge=1, le=5)


class MemoryCorrectionBody(StrictModel):
    content: str = Field(min_length=1, max_length=4000)
    kind: MemoryKind
    importance: int = Field(ge=1, le=5)
    reason: str = Field(min_length=1, max_length=500)


class MemoryResponse(BaseModel):
    id: str
    content: str
    kind: MemoryKind
    importance: int
    source: str
    created_at: str
    updated_at: str
    status: Literal["active", "superseded"] = "active"
    reinforcement: int = 1
    supersedes_id: str | None = None
    last_reinforced_at: str | None = None
    source_ref: str | None = None
    valid_at: str | None = None
    invalid_at: str | None = None
    attributes: dict = Field(default_factory=dict)
    entities: list = Field(default_factory=list)
    provenance: dict = Field(default_factory=dict)


class MemoryReindexResponse(BaseModel):
    enabled: bool
    indexed: int
    remaining: int


class MemoryWriteResponse(BaseModel):
    action: Literal["created", "reinforced", "superseded"]
    memory: MemoryResponse
    previous_id: str | None = None
    reason: str = ""


class MemoryReplacementResponse(BaseModel):
    id: int
    old_memory_id: str
    new_memory_id: str
    old_content: str
    new_content: str
    relation: str
    reason: str
    created_at: str


class MemoryTimelineResponse(MemoryResponse):
    replacement_reason: str | None = None
    replacement_relation: str | None = None


class TraceResponse(BaseModel):
    id: str
    session_id: str
    status: str
    steps: int
    duration_ms: int
    memories: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: str


class ChatResponse(BaseModel):
    message: MessageResponse
    memories_created: list[MemoryResponse]
    trace: TraceResponse


class CancelResponse(BaseModel):
    status: Literal["cancelled", "idle"]
    session_id: str


class MemoryUndoBody(StrictModel):
    source_refs: list[str] = Field(min_length=1, max_length=100)
    dry_run: bool = False


class MemoryUndoResponse(BaseModel):
    affected_ids: list[str]
    restored_ids: list[str]


class MemoryJobResponse(BaseModel):
    id: str
    source_ref: str
    status: str
    attempts: int
    error: str | None = None
    available_at: str | None = None
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    created_at: str
    updated_at: str
    extracted_count: int | None = None
    staged_count: int | None = None


class MemoryReviewResponse(BaseModel):
    id: str
    job_id: str
    source_ref: str
    ordinal: int
    content: str
    kind: MemoryKind
    importance: int
    status: Literal["pending", "applying", "approved", "rejected"]
    applied_memory_id: str | None = None
    applied_action: str | None = None
    applied_claim_id: str | None = None
    assessment: dict[str, Any] | None = None
    created_at: str
    updated_at: str


class MemoryReviewApproval(StrictModel):
    content: str = Field(min_length=1, max_length=4000)
    kind: MemoryKind
    importance: int = Field(ge=1, le=5)
    claim: ClaimDraft | None = None
    approve_claim: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def explicit_claim_approval(self):
        if (self.claim is not None) != self.approve_claim:
            raise ValueError("结构化状态须同时提交完整Claim和approve_claim=true明确批准")
        return self


class MemoryReviewProposal(StrictModel):
    message_id: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=4000)
    kind: MemoryKind = "fact"
    importance: int = Field(default=3, ge=1, le=5)


class EpisodePatch(StrictModel):
    pin: bool | None = Field(default=None, strict=True)
    archive: bool | None = Field(default=None, strict=True)
    reason: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def one_action(self):
        fields = self.model_fields_set
        if fields == {"pin"} and type(self.pin) is bool:
            return self
        if fields in ({"archive"}, {"archive", "reason"}) and self.archive is True:
            return self
        raise ValueError("仅可提交 pin 布尔值，或 archive=true 及可选 reason")


class EpisodeMaintenanceBody(StrictModel):
    dry_run: bool = Field(default=True, strict=True)


class MessageSourceResponse(BaseModel):
    message_id: str
    session_id: str
    session_title: str
    role: str
    content: str
    created_at: str
    origin: dict = Field(default_factory=dict)


class ToolExecuteBody(StrictModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    confirm_write: bool = False


class ToolExecuteResponse(BaseModel):
    name: str
    ok: bool
    content: str
    elapsed_ms: int


class ToolSearchBody(StrictModel):
    query: str = Field(min_length=1, max_length=200)
    top_k: int = Field(default=5, ge=1, le=10)
    allowed_risk: list[Literal["read-only", "write", "external-side-effect"]] | None = None


class DriftRunBody(StrictModel):
    force: bool = False


class ModelSlotBody(StrictModel):
    model: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)


class ModelsUpdateBody(StrictModel):
    main: ModelSlotBody | None = None
    fast: ModelSlotBody | None = None
    embedding: ModelSlotBody | None = None


class ModelTestBody(StrictModel):
    slot: Literal["main", "fast", "embedding"] = "main"


class MarkdownWriteBody(StrictModel):
    content: str = Field(min_length=1, max_length=200000)


class EvolveBody(StrictModel):
    new_info: str = Field(min_length=1, max_length=4000)
    reason: str = Field(default="", max_length=500)
