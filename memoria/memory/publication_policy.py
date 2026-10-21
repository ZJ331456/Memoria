"""Authority policy for pending state revisions. Runs in a single SQLite snapshot."""

import json
from ..claims import ClaimError, sha
from ..persistence.reviews import ReviewRepository


class PublicationPolicy:
    def __init__(self, claims):
        self.claims = claims
        self.store = claims.store
        self.reviews = ReviewRepository(claims.store)

    def guard(self, result, request):
        with self.store.memory_transaction():
            return self._guard(result, request)

    def _guard(self, result, request):
        # A validated conflicting assertion cannot overwrite a Claim, nor silently let
        # the final Reader present the old state as uncontested current knowledge.
        if (
            request.fact_as_of
            or request.recorded_as_of
            or getattr(request, "intent", None) == "history"
            or result["path"] == "history"
        ):
            return result
        state = result.get("state", result if "payload" in result else None)
        claims = {c["id"]: c for c in state["payload"]["claims"]} if state else {}
        pending = []
        if claims:
            rows = self.reviews.pending_targets(list(claims))
            for row in rows:
                report = json.loads(row["data_json"])
                proposal = report.get("structured_proposal") or {}
                target = claims.get(proposal.get("target_claim_id"))
                if (
                    not target
                    or target["revision_id"] != proposal.get("expected_revision_id")
                    or sha(row["content"]) != proposal.get("candidate_sha256")
                ):
                    continue
                try:
                    self.claims._evidence({"evidence": proposal["evidence"]})
                except (ClaimError, KeyError):
                    continue
                pending.append(
                    {
                        "review_id": row["id"],
                        "claim_id": target["id"],
                        "revision_id": target["revision_id"],
                        "source_ref": row["source_ref"],
                        "reason": "source_bound_conflicting_assertion_awaits_revision_review",
                    }
                )
        if pending:
            reason = result["reason"]
            if "unresolved revision proposal" not in reason:
                reason += "; unresolved revision proposal"
            result.update(path="clarify", reason=reason, pending_revisions=pending)
            if result.get("production_trace"):
                from ..observability.memory_trace import fingerprint

                trace = {
                    **result["production_trace"],
                    "publication_gate": {"path": "clarify", "pending_revisions": pending},
                }
                trace["trace_sha256"] = fingerprint(
                    {k: v for k, v in trace.items() if k != "trace_sha256"}
                )
                result["production_trace"] = trace
        return result
