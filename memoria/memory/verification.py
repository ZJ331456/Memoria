"""Bounded contract repair; incomplete text never becomes semantic evidence."""
from __future__ import annotations

from ..llm.types import ContextLengthError, IncompleteResponseError

REPAIR_INSTRUCTION = (
    '\nRegenerate the COMPLETE JSON from the same evidence. The previous completion was invalid or truncated. '
    'Do not continue a partial JSON. Keep all necessary facts/answer units, use minimal complete verbatim quotes '
    'and short reasons. Never fabricate quotes, missing facts or conflicts. Missing_fact must list an explicit '
    'missing requirement; conflict must list a conflicted requirement with two distinct sources. '
    'An uncertain negative judgment must not be promoted to sufficient during repair; use unsupported if needed.')


async def verify(call, stage, system, packet, validate, errors):
    from .reader import strict_json
    forbid_sufficient = False
    for attempt in range(2):
        value = None
        try:
            result = await call(stage, system + (REPAIR_INSTRUCTION if attempt else ''), packet)
            if result is None:
                raise ContextLengthError('Verification budget unavailable')
            if result.finish_reason not in {None, 'stop'} or not result.content.strip():
                raise IncompleteResponseError(result.finish_reason)
            if result.tool_calls:
                raise ValueError('Verifier tool calls forbidden')
            value = strict_json(result.content)
            decision = validate(value)
            if forbid_sufficient and decision.get('state') == 'sufficient':
                raise ValueError('Repair cannot promote an inconsistent negative gate')
            return decision
        except (ValueError, TypeError) as exc:
            if isinstance(exc, IncompleteResponseError) and exc.finish_reason != 'length':
                raise
            if isinstance(value, dict) and value.get('state') in {'missing_fact', 'conflict'}:
                forbid_sufficient = True
            errors.append({'stage':stage, 'attempt':attempt + 1, 'error':type(exc).__name__,
                           'code':'output_length' if isinstance(exc, IncompleteResponseError) else 'invalid_contract'})
            if attempt:
                raise


async def support_envelope(meter, system, packet, *, selected=None, max_tokens=2048, deepseek_cache=False, shared_reader=None, **generation):
    """4000 answer characters x six JSON bytes; include all possible cited sources and repair text."""
    from ..llm.deepseek_cache import evidence_messages
    messages = evidence_messages(selected or meter.settings.main,system+REPAIR_INSTRUCTION,
        {**packet,'proposed_answer':'\x00'*4000},enabled=deepseek_cache)
    if shared_reader is not None:
        from ..llm.deepseek_cache import shared_reader_messages
        reader_system,reader_packet=shared_reader
        messages=shared_reader_messages(selected or meter.settings.main,reader_system,reader_packet,
            system+REPAIR_INSTRUCTION,{**packet,'proposed_answer':'\x00'*4000})
    return await meter.measure(selected or meter.settings.main, messages, None, max_tokens, **generation)
