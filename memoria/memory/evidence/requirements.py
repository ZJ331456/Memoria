"""Requirements use query/parser output only. No gold, target IDs or annotations."""
from __future__ import annotations
import re


def requirements_for(query: str, parsed: dict) -> dict:
    facets = ["slot:" + p for p in parsed["predicates"]]
    facets += ["condition:" + k for k in sorted(parsed["context"])]
    if parsed["intent"] in {"history", "transition"}:
        facets.append("time:" + parsed["axis"])
    for axis in ("fact","recorded"):
        if parsed.get(axis+"_as_of") and "time:"+axis not in facets:
            facets.append("time:"+axis)
    for side in ("from", "to"):
        if parsed.get("transition_" + side):
            facets.append("transition:" + side)
    # Open evidence terms are literal substrings, never inferred gold source IDs.
    terms = list(dict.fromkeys(re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{1,30}|[\u4e00-\u9fff]{2,12}", query)))[:8]
    if not facets:
        # Literal query bigrams are a bounded lexical baseline; not a semantic judge.
        terms = list(dict.fromkeys(t if t.isascii() else t[i:i+2] for t in terms
                                  for i in (range(len(t)-1) if not t.isascii() else [0])
                                  if (t if t.isascii() else t[i:i+2]) not in {"什么","哪里","我的","哪些","多少","时候"}))[:16]
    if not facets:
        facets = ["topic:" + term for term in terms]
    return {"version": "query-requirements-rules-v1", "origin": "query_and_parser_only",
            "facets": facets, "literal_terms": terms, "semantic_sufficiency_verified": False}


def coverage(item: dict, requirements: dict, parsed: dict) -> list[str]:
    """Mechanical evidence features, not proof that a source entails an answer."""
    data = item["data"] if isinstance(item["data"], list) else [item["data"]]
    found = set()
    for value in data:
        if not isinstance(value, dict):
            continue
        predicate = value.get("predicate")
        if predicate:
            found.add("slot:" + predicate)
        for key, context in parsed["context"].items():
            scope = value.get("scope", {})
            if key in scope and type(scope[key]) is type(context) and scope[key] == context:
                found.add("condition:" + key)
        if value.get("recorded_at") and parsed["axis"] == "recorded":
            found.add("time:recorded")
        if value.get("fact_start") or value.get("event_time"):
            found.add("time:fact")
        text = " ".join(str(value.get(k, "")) for k in ("value", "text"))
        text += " " + " ".join(e.get("quote", "") for e in value.get("evidence", []) if isinstance(e, dict)) if isinstance(value.get("evidence"), list) else ""
        for side in ("from", "to"):
            anchor = parsed.get("transition_" + side)
            if anchor and anchor in text:
                found.add("transition:" + side)
        for term in requirements["literal_terms"]:
            if term.casefold() in text.casefold():
                found.add("topic:" + term)
    return sorted(found & set(requirements["facets"]))
