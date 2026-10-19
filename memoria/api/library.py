"""All routes are mounted only inside the authenticated account runtime."""
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from ..memory.library import SourceInput, RevisionInput, FeedbackInput


class SearchInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    query: str=Field(min_length=1,max_length=2000)
    session_id: str | None=Field(default=None,max_length=64)
    limit: int=Field(default=5,ge=1,le=8)
    budget: int=Field(default=4000,ge=800,le=8000)


def library_router(library, kernel=None):
    router=APIRouter(prefix='/api/library',tags=['non-conversation memory'])

    @router.get('/sources')
    def sources(limit: int=Query(default=50,ge=1,le=200)):
        return library.list(limit)

    @router.post('/sources',status_code=201)
    def create(body: SourceInput):
        return kernel.ingest(body) if kernel else library.create(body)

    @router.post('/search')
    def search(body: SearchInput):
        return library.search(**body.model_dump())

    @router.get('/sources/{sid}')
    def detail(sid: str):
        return library.detail(sid)

    @router.post('/sources/{sid}/revisions')
    def revise(sid: str,body: RevisionInput):
        return library.revise(sid,body)

    @router.get('/sources/{sid}/revisions/{rid}')
    def version(sid: str,rid: str):
        return library.version(sid,rid)

    @router.post('/sources/{sid}/retract')
    def retract(sid: str):
        return library.retract(sid)

    @router.post('/sources/{sid}/feedback',status_code=201)
    def feedback(sid: str,body: FeedbackInput):
        return library.feedback(sid,body)

    @router.post('/sources/{sid}/distill')
    async def distill(sid: str):
        return await library.distill(sid)

    return router
