"""Structured memory endpoints; tenant identity is supplied only by the gateway."""
from fastapi import APIRouter, Query

from ..claims import (AccessRequest, ClaimDecision, ClaimDraft, ClaimMemory,
                      ClaimRevision, ExtractionRequest, SLOTS, ViewRequest)


def claims_router(memory: ClaimMemory | None, llm=None) -> APIRouter:
    router = APIRouter(tags=["claims and views"])

    @router.get("/api/claim-slots")
    def slots():
        return {"slots":[{"predicate":name,"multiple":multiple} for name,multiple in SLOTS.items()],
                "scope_semantics":"exact_typed_conjunction","default_subject":"self"}

    @router.get("/api/claims")
    def listing(limit: int = Query(default=100,ge=1,le=500)):
        return memory.list(limit)

    @router.post("/api/claims",status_code=201)
    def create(body: ClaimDraft):
        return memory.create(body)

    @router.post("/api/claims/extract",status_code=201)
    async def extract(body: ExtractionRequest):
        return await memory.extract(llm,body)

    @router.get("/api/claims/{claim_id}")
    def get(claim_id: str):
        return memory.get(claim_id)

    @router.get("/api/claims/{claim_id}/history")
    def history(claim_id: str):
        return memory.history(claim_id)

    @router.post("/api/claims/{claim_id}/revisions",status_code=201)
    def revise(claim_id: str,body: ClaimRevision):
        return memory.revise(claim_id,body)

    @router.post("/api/claims/{claim_id}/approve")
    def approve(claim_id: str,body: ClaimDecision):
        return memory.approve(claim_id,body)

    @router.post("/api/claims/{claim_id}/retract")
    def retract(claim_id: str,body: ClaimDecision):
        return memory.decide(claim_id,body,"retract")

    @router.post("/api/claims/{claim_id}/reject")
    def reject(claim_id: str,body: ClaimDecision):
        return memory.decide(claim_id,body,"reject")

    @router.post("/api/claims/{claim_id}/dispute")
    def dispute(claim_id: str,body: ClaimDecision):
        return memory.decide(claim_id,body,"dispute")

    @router.post("/api/claims/{claim_id}/restore")
    def restore(claim_id: str,body: ClaimDecision):
        return memory.decide(claim_id,body,"restore")

    @router.delete("/api/claims/{claim_id}",status_code=204)
    def delete(claim_id: str):
        memory.delete(claim_id)

    @router.post("/api/claim-views",status_code=201)
    def compile_view(body: ViewRequest):
        return memory.compile(body)

    @router.get("/api/claim-views/{view_id}")
    def view(view_id: str):
        return memory.view(view_id)

    @router.post("/api/memory-access")
    def access(body: AccessRequest):
        return memory.access(body)

    return router
