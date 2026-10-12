"""Public selector API; budget and selection contracts live in evidence/."""
from __future__ import annotations

from ..claims import canonical
from .evidence import EvidenceBudget, select


def select_evidence(candidates: list[dict], requirements: list[str], budget_chars: int,
                    strategy: str = "coverage", limit: int = 8, *, budget: EvidenceBudget | None = None) -> dict:
    result = select(candidates, requirements, budget or EvidenceBudget(budget_chars), strategy, limit)
    result.update(cost_chars=len(canonical(result["payload"])), budget_chars=budget_chars,
                  cost_unit=result["budget"]["unit"],
                  dropped=[{"id": t["id"], "reason": t["decision"]} for t in result["trace"] if t["decision"] != "selected"])
    return result
