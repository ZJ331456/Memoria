"""Privacy projection for live turn storage; isolated experiment artifacts stay raw."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()


def compact_metadata(metadata):
    value = deepcopy(metadata or {})
    calls = value.get("llm_calls")
    if calls is None:
        calls = value.get("reader_calls", value.get("adaptive_retrieval", {}).get("calls", []))
    calls = calls if isinstance(calls, list) else []
    call_digest = digest(calls)
    private_fields = {"messages", "raw", "response", "prompt", "content", "quote", "text"}

    def project(item, *, root=False):
        if isinstance(item, list):
            return [project(child) for child in item]
        if not isinstance(item, dict):
            return item
        result = {}
        for key, child in item.items():
            if key in {"llm_calls", "reader_calls", "calls"} and isinstance(child, list):
                if not (root and key == "llm_calls") and digest(child) == call_digest:
                    result[key] = {"ref": "llm_calls", "count": len(child)}
                    continue
            if key in private_fields and child is not None:
                result[key + "_sha256"] = digest(child)
                result[key + "_chars"] = (
                    len(child)
                    if isinstance(child, str)
                    else len(json.dumps(child, ensure_ascii=False, default=str))
                )
            else:
                result[key] = project(child)
        # The hash describes the persisted projection, rather than its private predecessor.
        if "trace_sha256" in result:
            result["trace_sha256"] = digest(
                {k: v for k, v in result.items() if k != "trace_sha256"}
            )
        return result

    value.pop("reader_calls", None)
    value["llm_calls"] = calls
    result = project(value, root=True)
    result["trace_storage_policy"] = "hashes-and-cost-v1; raw experiment logs are separate"
    return result


def call_metrics(calls):
    """Known tokens are a lower bound; failures with missing usage are never zero usage."""
    llm = [c for c in calls if isinstance(c, dict) and c.get("stage") != "embedding"]
    embedding = [c for c in calls if isinstance(c, dict) and c.get("stage") == "embedding"]
    return {
        "llm_requests": len(llm),
        "llm_retries": sum(int(c.get("retries", 0) or 0) for c in llm),
        "llm_duration_ms": sum(int(c.get("duration_ms", c.get("elapsed_ms", 0)) or 0) for c in llm),
        "llm_tokens": sum(int((c.get("usage") or {}).get("total_tokens", 0) or 0) for c in llm),
        "llm_usage_unknown": sum(not bool(c.get("usage")) for c in llm),
        "llm_retries_unknown": sum(c.get("retries") is None for c in llm),
        "embedding_requests": len(embedding),
        "embedding_usage_unknown": sum(not bool(c.get("usage")) for c in embedding),
    }
