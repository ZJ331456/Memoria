"""Greedy relevance/coverage/redundancy under an indivisible evidence budget."""
from __future__ import annotations
import math
import re
from .budget import EvidenceBudget, serialize
from .trace import decision


def terms(item):
    # Ignore IDs/hash/metadata so repeated bookkeeping doesn't fake redundancy.
    data = item.get("data", {})
    entries = data if isinstance(data, list) else [data]
    text = " ".join(str(c.get(k, "")) for c in entries for k in ("text", "value", "predicate") if isinstance(c, dict))
    tokens = set(re.findall(r"[a-zA-Z0-9_]+", text.casefold()))
    chinese = re.findall(r"[\u4e00-\u9fff]+", text)
    tokens.update(t[i:i+2] for t in chinese for i in range(max(1, len(t)-1)))
    return tokens


def similarity(a, b):
    return len(a & b) / len(a | b) if a | b else 0.0


def select(candidates, requirements, budget: EvidenceBudget, strategy="evidence_coverage", limit=8,
           alpha=1.0, beta=1.0, gamma=0.35):
    if strategy not in {"topk", "coverage", "mmr", "evidence_coverage"}:
        raise ValueError("unsupported selector")
    if len({c["id"] for c in candidates}) != len(candidates):
        raise ValueError("candidate IDs must be unique")
    required, covered, sources = set(requirements), set(), set()
    chosen, trace, remaining = [], [], list(enumerate(candidates))
    scores = [float(c.get("score", 0)) for c in candidates]
    if not all(math.isfinite(s) for s in scores):
        raise ValueError("candidate score must be finite")
    minimum, maximum = min(scores, default=0), max(scores, default=0)
    normalized = [(s-minimum)/(maximum-minimum) if maximum > minimum else 1.0 for s in scores]
    features = [terms(c) for c in candidates]
    envelope = lambda items: {"evidence": items, "requirements": requirements}
    empty_cost = budget.cost(envelope([]))
    if empty_cost > budget.limit:
        return {"payload": {"evidence": [], "requirements": []}, "covered": [], "uncovered": sorted(required),
                "cost": budget.cost({"evidence": [], "requirements": []}), "budget": budget.public(), "strategy": strategy,
                "trace": [decision(c, "requirements_envelope_over_budget") for c in candidates],
                "candidate_ids": [c["id"] for c in candidates], "semantic_sufficiency_verified": False}
    while remaining and len(chosen) < limit:
        evaluated = []
        current_cost = budget.cost(envelope(chosen))
        for index, item in remaining:
            new = set(item.get("covers", [])) & required - covered
            redundancy = max((similarity(features[index], terms(c)) for c in chosen), default=0.0)
            marginal = budget.cost(envelope(chosen+[item]))-current_cost
            utility = alpha*normalized[index]+beta*len(new)/max(1,len(required))-gamma*redundancy
            # Size-normalized gain permits complementary short evidence within the same budget.
            rank = ((len(new), scores[index]) if strategy == "coverage" else
                    (scores[index],) if strategy == "topk" else
                    (0.5*normalized[index]-0.5*redundancy,) if strategy == "mmr" else
                    (utility/max(1,marginal),))
            evaluated.append((rank, -index, index, item, new, redundancy, marginal, utility))
        rank, _, index, item, new, redundancy, marginal, utility = max(evaluated, key=lambda e:(e[0],e[1]))
        remaining = [(i,c) for i,c in remaining if i != index]
        reason = ("duplicate_source" if item["kind"] == "source" and set(item["source_ids"]) & sources else
                  "budget" if budget.cost(envelope(chosen+[item])) > budget.limit else "selected")
        trace.append(decision(item, reason, len(chosen)+1 if reason == "selected" else None,
                              gain=round(utility,6), ranking_value=[round(v,6) for v in rank], marginal_cost=marginal,
                              newly_covered=sorted(new), redundancy=round(redundancy,6)))
        if reason == "selected":
            chosen.append(item); covered.update(new); sources.update(item["source_ids"])
    trace += [decision(item, "top_k") for _, item in remaining]
    payload = envelope(chosen)
    return {"payload": payload, "strategy": strategy, "cost": budget.cost(payload), "budget": budget.public(),
            "covered": sorted(covered), "uncovered": sorted(required-covered), "trace": trace,
            "candidate_ids": [c["id"] for c in candidates], "semantic_sufficiency_verified": False,
            "objective": {"alpha":alpha,"beta":beta,"gamma":gamma,"mmr_lambda":0.5,"redundancy":"lexical_bigram_jaccard; not dense MMR"}}
