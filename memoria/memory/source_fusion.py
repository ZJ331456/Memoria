"""Bounded channel diversity on top of source RRF, never semantic verification.

The same evidence limit applies. High consensus ranks keep half the slots;
channel-prefix alternatives compete for the remainder without duplicate votes.
No question, gold annotation, generated answer or source body is inspected here.
"""
from __future__ import annotations

POLICY = 'rrf-core-and-channel-prefix-v1'
PREFIX_DEPTH = 4


def choose(ordered, lanes, limit):
    """Return a deterministic bounded union and its selection reasons.

    ``ordered`` is the live RRF ordering. ``lanes`` contains only active channels,
    in their declared order; inactive modes cannot steer selection. Prefix rescue
    is deliberately bounded to four ranks, so a weak channel cannot replace the
    whole consensus with arbitrarily deep hits. This is an inspection opportunity,
    not a claim that a protected passage answers the question.
    """
    ordered = list(dict.fromkeys(ordered))
    capacity = min(max(0, limit), len(ordered))
    active_channels = sum(bool(lane) for lane in lanes.values())
    core = ordered[:capacity if active_channels < 2 else (capacity + 1) // 2]
    selected = set(core)
    baseline = set(ordered[:capacity])
    eligible = set(ordered)
    prefixes = {name: list(dict.fromkeys(mid for mid in lane if mid in eligible))[:PREFIX_DEPTH]
                for name, lane in lanes.items()}
    complementary, protected = [], {}
    while len(selected) < capacity:
        progress = False
        for name, prefix in prefixes.items():
            # A prefix hit already within the original RRF cutoff will compete
            # normally in the fill step; only actual crop losses spend rescue
            # slots. Identical channels therefore preserve the original order.
            other = next((mid for mid in prefix if mid not in selected and mid not in baseline), None)
            if other is None:
                continue
            selected.add(other)
            complementary.append(other)
            protected[other] = [name]
            progress = True
            if len(selected) == capacity:
                break
        if not progress:
            break
    # Report all prefix channels represented by a rescued passage, even when
    # another channel encountered it first. Duplicate lanes do not spend slots.
    for mid in complementary:
        protected[mid] = [name for name, prefix in prefixes.items() if mid in prefix]
    fill = [mid for mid in ordered if mid not in selected][:capacity - len(selected)]
    # Interleave rather than append the lower-RRF alternatives at a tail that a
    # later bounded gate is likely to discard before seeing them.
    result = []
    for index in range(max(len(core), len(complementary))):
        if index < len(core):
            result.append(core[index])
        if index < len(complementary):
            result.append(complementary[index])
    result.extend(fill)
    ranks = {mid: rank for rank, mid in enumerate(ordered, 1)}
    reasons = {mid: {'fused_rank': ranks[mid], 'selection_rank': rank,
                    'selection_reason': 'rrf_core' if mid in core else
                        'channel_prefix' if mid in protected else 'rrf_fill',
                    'protected_channels': protected.get(mid, [])}
               for rank, mid in enumerate(result, 1)}
    return result, reasons
