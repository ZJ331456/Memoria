"""Recheck an already validated Reader answer immediately before a host publishes it."""

from copy import deepcopy

from .access import QueryRequest
from .reader import CitationGuard, SAFE_REJECTED


def revalidate(kernel, request, result, *, answer=None):
    frozen = deepcopy(result)
    reason = None
    if answer is not None and answer != frozen["answer"]:
        reason = "post_hook_changed_answer"
    elif frozen["status"] == "answer":
        with kernel.store.memory_transaction():
            if isinstance(request, QueryRequest):
                guard = CitationGuard(
                    kernel.claims,
                    frozen["access"],
                    request,
                    access_policy=lambda: bool(
                        kernel.guard_pending_revisions(deepcopy(frozen["access"]), request).get(
                            "pending_revisions"
                        )
                    ),
                )
                reason = guard.freshness()
            elif any(
                not kernel.dependency_valid(d, session_id=request.session_id)
                for d in frozen["access"].get("dependencies", [])
            ):
                reason = "source_changed_before_publish"
    if reason:
        frozen.update(
            status="abstain",
            answer=SAFE_REJECTED,
            citations=[],
            validation={"valid": False, "reason": reason, "semantic_entailment_verified": False},
        )
    return frozen
