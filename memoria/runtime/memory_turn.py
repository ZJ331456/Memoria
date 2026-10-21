"""Run memory turn for the tenant runtime."""

from __future__ import annotations
from ..lifecycle import Phase


async def run_memory_turn(
    self,
    kernel,
    request,
    context,
    tracer,
    user_message,
    write_message,
    on_event,
    skip_memory_enqueue,
    turn_kind,
    turn_id,
):
    """Chat/bots use the same buffered Kernel loop, with normal hooks and publication gates."""
    from copy import deepcopy
    from ..memory.reader_calls import observe_requests

    session_id = context.session_id
    await self.pipeline.run(Phase.BEFORE_REASONING, context)
    await self.event_bus.emit(
        "turn.before_reasoning", {"session_id": session_id, "turn_id": turn_id}
    )
    with tracer.timings.measure("memory.kernel_answer"), observe_requests(
        tracer.mark_llm_request, timings=tracer.timings
    ):
        result = deepcopy(await kernel.answer(request))
    access = result["access"]
    context.response = result["answer"]
    context.metadata.update(
        memory_closed_loop=deepcopy(result["closed_loop"]),
        citation_validation=deepcopy(result["validation"]),
        citations=deepcopy(result["citations"]),
        reader_budget=deepcopy(result.get("reader_budget")),
    )
    if "adaptive" in access:
        context.metadata.update(
            adaptive_retrieval=deepcopy(access["adaptive"]),
            reader_calls=deepcopy(access["adaptive"]["calls"]),
        )
    elif result.get("reader_calls"):
        context.metadata["reader_calls"] = deepcopy(result["reader_calls"])
        context.metadata["llm_calls"] = deepcopy(result["reader_calls"])
    context.metadata["claim_access"] = deepcopy(
        {
            "production_trace": access.get("production_trace"),
            "path": access["path"],
            "reason": access.get("reason"),
            "parsed": access.get("parsed"),
            "candidate_ids": access.get("selection", {}).get("candidate_ids", []),
            "selected_ids": [
                c["id"] for c in access.get("selection", {}).get("payload", {}).get("evidence", [])
            ],
            "cost_chars": access.get("selection", {}).get("cost_chars"),
            "timings": access.get("timings"),
            "rejected": access.get("rejected"),
            "dropped": access.get("selection", {}).get("dropped", []),
        }
    )
    await self.pipeline.run(Phase.AFTER_REASONING, context)
    with self.store._memory_transaction():
        verdict = kernel.revalidate_answer(request, result, answer=context.response)
        context.response = verdict["answer"]
        context.metadata.update(
            citation_validation=verdict["validation"], citations=verdict["citations"]
        )
        assistant_message = write_message(session_id, "assistant", context.response)
    # Final buffered text is emitted only after hook/source/identity/version checks.
    if on_event:
        tracer.mark_visible_text(context.response)
        await on_event({"type": "delta", "content": context.response})
    context.metadata["memory_closed_loop"] = deepcopy(result["closed_loop"])
    context.metadata["memory_closed_loop"]["chat_publication"] = {
        "validation": context.metadata["citation_validation"],
        "citations": context.metadata["citations"],
    }
    from ..observability.memory_trace import fingerprint

    closed = context.metadata["memory_closed_loop"]
    closed["trace_sha256"] = fingerprint({k: v for k, v in closed.items() if k != "trace_sha256"})
    if not skip_memory_enqueue:
        job = self.store.enqueue_memory_job(user_message["id"], context.user_text, context.response)
        context.metadata["memory_job"] = {
            "id": job["id"],
            "source_ref": user_message["id"],
            "status_at_enqueue": job["status"],
        }
    await self.pipeline.run(Phase.AFTER_TURN, context)
    await self.event_bus.emit(
        "turn.after",
        {"session_id": session_id, "turn_id": turn_id, "status": "completed", "steps": 1},
    )
    trace = tracer.finish("completed", 1, [], [], metadata=context.metadata)
    self._record_episode_safe(
        turn_kind,
        session_id,
        user_message,
        assistant_message,
        context.user_text,
        "completed",
        context.response,
        "",
        [],
        trace["id"],
    )
    return assistant_message, [], trace
