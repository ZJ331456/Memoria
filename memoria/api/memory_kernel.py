"""Authenticated, application-independent memory data plane."""
from fastapi import APIRouter, Query

from ..memory.library import SourceInput
from ..memory.records import Domain, MemorySearch,KnowledgeAnswer


def memory_kernel_router(kernel):
    router = APIRouter(prefix='/api/memory-kernel', tags=['memory kernel'])

    @router.post('/sources', status_code=201)
    def ingest(body: SourceInput):
        return kernel.ingest(body)

    @router.post('/search')
    async def search(body: MemorySearch):
        return await kernel.search(body)

    @router.post('/knowledge-answer')
    async def answer(body: KnowledgeAnswer):
        return await kernel.answer(body)

    @router.get('/lineage/{domain}/{source_id}')
    def lineage(domain: Domain,source_id: str):
        return kernel.lineage([{'domain':domain,'id':source_id}])[0]

    @router.get('/sources/{domain}/{source_id}')
    def read(domain: Domain, source_id: str,
             session_id: str | None = Query(default=None, min_length=1, max_length=64)):
        return kernel.read(domain, source_id, session_id=session_id)

    return router
