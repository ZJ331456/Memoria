"""All routes are mounted only inside the authenticated account runtime."""

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from .kernel_dependency import require_kernel

from ..memory.library import SourceInput, RevisionInput, FeedbackInput
from ..memory.source_formation import FormRequest


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=5, ge=1, le=8)
    budget: int = Field(default=4000, ge=800, le=8000)


def library_router(kernel):
    """Source management routes; every operation goes through the Kernel facade.

    The facade delegates into library/formation where domain transactions live,
    so future kernel-level auditing cannot be bypassed by an API adapter.
    """
    router = APIRouter(
        prefix="/api/library",
        tags=["non-conversation memory"],
        dependencies=[require_kernel(kernel)],
    )

    @router.get("/sources")
    def sources(limit: int = Query(default=50, ge=1, le=200)):
        return kernel.sources(limit)

    @router.post("/sources", status_code=201)
    def create(body: SourceInput):
        return kernel.ingest(body)

    @router.post("/search")
    def search(body: SearchInput):
        return kernel.search_sources(**body.model_dump())

    @router.get("/sources/{sid}")
    def detail(sid: str):
        return kernel.source_detail(sid)

    @router.post("/sources/{sid}/revisions")
    def revise(sid: str, body: RevisionInput):
        return kernel.revise_source(sid, body)

    @router.get("/sources/{sid}/revisions/{rid}")
    def version(sid: str, rid: str):
        return kernel.source_version(sid, rid)

    @router.post("/sources/{sid}/retract")
    def retract(sid: str):
        return kernel.retract_source(sid)

    @router.post("/sources/{sid}/feedback", status_code=201)
    def feedback(sid: str, body: FeedbackInput):
        return kernel.source_feedback(sid, body)

    @router.post("/sources/{sid}/distill")
    async def distill(sid: str):
        return await kernel.distill_source(sid)

    @router.post("/sources/{sid}/form")
    async def form(sid: str, body: FormRequest):
        return await kernel.form_source(sid, body)

    @router.get("/sources/{sid}/observations")
    def observations(sid: str):
        return kernel.source_observations(sid)

    @router.post("/observations/{oid}/consolidate")
    async def consolidate(oid: str):
        return await kernel.consolidate_observation(oid)

    @router.get("/consolidations")
    def consolidations(session_id: str | None = Query(default=None, min_length=1, max_length=64)):
        return kernel.consolidations(session_id=session_id)

    @router.post("/consolidations/{key}/retire")
    def retire(key: str, body: Retirement):
        return kernel.retire_consolidation(key, body.expected_revision_id, session_id=body.session_id)

    return router


class Retirement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision_id: str = Field(min_length=1, max_length=64)
    session_id: str | None = Field(default=None, min_length=1, max_length=64)
