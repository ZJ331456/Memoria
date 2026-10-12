"""Fixed-pool, whole-evidence selection. Character cost is explicitly not tokens."""
from __future__ import annotations

from ..claims import canonical


def select_evidence(candidates: list[dict], requirements: list[str], budget_chars: int,
                    strategy: str = "coverage", limit: int = 8) -> dict:
    if strategy not in {"topk", "coverage"}:
        raise ValueError("unsupported selector")
    remaining = list(enumerate(candidates))
    remaining.sort(key=lambda pair: (-float(pair[1].get("score", 0)), pair[0]))
    chosen, dropped, covered, sources = [], [], set(), set()
    # Budget the complete JSON envelope, including conditions, hashes and evidence.
    envelope = lambda items: {"evidence": items, "requirements": requirements}
    while remaining and len(chosen) < limit:
        if strategy == "coverage":
            remaining.sort(key=lambda pair: (
                -len(set(pair[1].get("covers", [])) & set(requirements) - covered),
                -float(pair[1].get("score", 0)), pair[0]))
        index, item = remaining.pop(0)
        source_ids = set(item["source_ids"])
        # Same-source Claim groups are atomic; never remove one polarity/condition.
        if item["kind"] == "source" and source_ids & sources:
            dropped.append({"id": item["id"], "reason": "duplicate_source"})
            continue
        if len(canonical(envelope(chosen + [item]))) > budget_chars:
            dropped.append({"id": item["id"], "reason": "budget"})
            continue
        chosen.append(item)
        covered.update(item.get("covers", []))
        sources.update(source_ids)
    dropped.extend({"id": item["id"], "reason": "top_k"} for _, item in remaining)
    payload = envelope(chosen)
    cost = len(canonical(payload))
    if cost > budget_chars:
        payload, cost = {"evidence": [], "requirements": []}, len(canonical({"evidence": [], "requirements": []}))
    return {"payload": payload, "strategy": strategy, "cost_chars": cost, "budget_chars": budget_chars,
            "cost_unit": "unicode_codepoints_json; not reader tokens", "covered": sorted(covered),
            "uncovered": sorted(set(requirements) - covered), "dropped": dropped,
            "candidate_ids": [c["id"] for c in candidates]}
