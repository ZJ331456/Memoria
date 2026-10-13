"""Bounded fallback policy, injected retrieval/reader; never a whole-history reader."""
from __future__ import annotations

from dataclasses import dataclass, field

VERSION = 'bounded-targeted-fallback-v1'


@dataclass
class AdaptiveFallback:
    evidence_budgets: tuple[int, ...] = (12000, 16000, 20000)
    max_calls: int = 6
    max_input_bytes: int = 160000
    calls: int = 0
    input_bytes: int = 0
    retrieval_rounds: int = 0
    searched_queries: set[str] = field(default_factory=set)
    trace: list[dict] = field(default_factory=list)

    def __post_init__(self):
        if (not 1 <= len(self.evidence_budgets) <= 3 or any(type(b) is not int or b < 256 or b > 24000 for b in self.evidence_budgets)
                or tuple(sorted(self.evidence_budgets)) != self.evidence_budgets
                or type(self.max_calls) is not int or not 1 <= self.max_calls <= 8
                or type(self.max_input_bytes) is not int or not 256 <= self.max_input_bytes <= 240000):
            raise ValueError('Invalid adaptive budget')

    def reserve_call(self, stage: str, prepared_report: dict):
        size = prepared_report['input_bytes']
        if type(size) is not int or size < 0 or not prepared_report['fits']:
            raise ValueError('Invalid complete request budget')
        if self.calls >= self.max_calls or self.input_bytes + size > self.max_input_bytes:
            self.trace.append({'stage': stage, 'decision': 'case_budget_exhausted'})
            return False
        self.calls += 1
        self.input_bytes += size
        self.trace.append({'stage': stage, 'decision': 'call_reserved', 'input_bytes': size,
                           'calls': self.calls, 'input_bytes_total': self.input_bytes})
        return True

    def plan(self, question: str, decision: dict, risk: dict) -> dict | None:
        if self.retrieval_rounds >= len(self.evidence_budgets) - 1:
            self.trace.append({'decision': 'retrieval_budget_exhausted'})
            return None
        queries = []
        for query in decision.get('missing_queries', [])[:2]:
            if not isinstance(query, str) or not 1 <= len(query.strip()) <= 300:
                raise ValueError('Invalid missing query')
            query = query.strip()
            if query.casefold() not in self.searched_queries and query not in queries:
                queries.append(query)
        if not queries:
            # Same query with a larger pool/context is an explicitly logged expansion.
            queries = [question]
        self.searched_queries.update(q.casefold() for q in queries)
        self.retrieval_rounds += 1
        plan = {'stage': self.retrieval_rounds, 'queries': queries,
                'route': 'related_timeline' if risk['requires_timeline'] else 'missing_fact',
                'evidence_chars': self.evidence_budgets[self.retrieval_rounds],
                'candidate_limit': 80, 'context_radius': 2 if risk['requires_timeline'] else 1,
                'reason': decision.get('state', decision.get('verdict', 'insufficient'))}
        self.trace.append({'decision': 'targeted_retrieval', **plan})
        return plan

    def public(self):
        return {'version': VERSION, 'calls': self.calls, 'input_bytes': self.input_bytes,
                'retrieval_rounds': self.retrieval_rounds, 'max_calls': self.max_calls,
                'max_input_bytes': self.max_input_bytes, 'evidence_budgets_chars': list(self.evidence_budgets),
                'full_history_reader_allowed': False, 'trace': self.trace}
