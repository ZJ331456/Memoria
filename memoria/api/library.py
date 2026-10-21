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
    library = kernel.library if kernel is not None else None
    router = APIRouter(
        prefix="/api/library",
        tags=["non-conversation memory"],
        dependencies=[require_kernel(kernel)],
    )

    @router.get("/sources")
    def sources(limit: int = Query(default=50, ge=1, le=200)):
        return library.list(limit)

    @router.post("/sources", status_code=201)
    def create(body: SourceInput):
        return kernel.ingest(body)

    @router.post("/search")
    def search(body: SearchInput):
        return library.search(**body.model_dump())

    @router.get("/sources/{sid}")
    def detail(sid: str):
        return library.detail(sid)

    @router.post("/sources/{sid}/revisions")
    def revise(sid: str, body: RevisionInput):
        return library.revise(sid, body)

    @router.get("/sources/{sid}/revisions/{rid}")
    def version(sid: str, rid: str):
        return library.version(sid, rid)

    @router.post("/sources/{sid}/retract")
    def retract(sid: str):
        return library.retract(sid)

    @router.post("/sources/{sid}/feedback", status_code=201)
    def feedback(sid: str, body: FeedbackInput):
        return library.feedback(sid, body)

    @router.post("/sources/{sid}/distill")
    async def distill(sid: str):
        return await library.distill(sid)

    @router.post("/sources/{sid}/form")
    async def form(sid: str, body: FormRequest):
        return await library.formation.form(sid, body)

    @router.get("/sources/{sid}/observations")
    def observations(sid: str):
        return library.formation.observations(sid)

    @router.post("/observations/{oid}/consolidate")
    async def consolidate(oid: str):
        return await library.formation.consolidate(oid)

    @router.get("/consolidations")
    def consolidations(session_id: str | None = Query(default=None, min_length=1, max_length=64)):
        if session_id is not None and not library.store.session(session_id):
            from ..memory.library import LibraryError

            raise LibraryError(404, "会话不存在")
        return library.formation.list(session_id=session_id)

    @router.post("/consolidations/{key}/retire")
    def retire(key: str, body: Retirement):
        return library.formation.retire(key, body.expected_revision_id, session_id=body.session_id)

    return router


class Retirement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision_id: str = Field(min_length=1, max_length=64)
    session_id: str | None = Field(default=None, min_length=1, max_length=64)
