"""Query-only, bounded evidence plan. Unknown identities/dates are never filled in."""
import re

VERSION = 'missing-requirements-plan-v1'
FACETS = (
    ('project', r'哪个项目|哪些项目|什么项目|which project', '项目名称及这些记录属于同一项目的原话依据'),
    ('adoption', r'采用|选用|使用.*方案|adopt|chose|choose|chosen|approach', '采用的方案及采用记录'),
    ('reason', r'为什么|为何|原因|理由|why|reason|rationale', '当时选择该方案的原因'),
    ('outcome', r'后来|后续|遇到.*问题|困难|教训|问题.*后来|subsequent|later|challenge|problem|outcome', '后续问题或结果及与前述项目/方案的关联'),
)


def complex_recall(query):
    return sum(bool(re.search(pattern, query, re.I)) for _, pattern, _ in FACETS) >= 2


def plan(query, parsed):
    facts = [{'fact': key + ': ' + text, 'kind': 'fact'} for key, pattern, text in FACETS
             if re.search(pattern, query, re.I)]
    if not facts:
        facts = [{'fact': 'answer: 完整回答原问题所需的事实及主体、条件、否定和时间依据', 'kind': 'fact'}]
    return {'version': VERSION, 'origin': 'query_and_parser_only', 'question': query,
        'requirements': facts, 'subject_id': parsed.get('subject_id'),
        'context': parsed.get('context', {}), 'negation': bool(re.search(r'不|没有|未|not|never', query, re.I)),
        'fact_interval': parsed.get('fact_interval'), 'recorded_interval': parsed.get('recorded_interval'),
        'time_precision': parsed.get('time_precision', 'unknown'),
        'unknowns_preserved': True, 'gold_used': False}


def validate_plan(value, evidence, query_plan):
    from ..evidence_sufficiency import EvidenceSufficiency
    decision = EvidenceSufficiency().validate(value, evidence)
    expected = [r['fact'] for r in query_plan['requirements']]
    actual = [r['fact'] for r in decision['requirements']]
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise ValueError('All frozen query requirements must be assessed exactly once')
    if any(r['kind'] != 'fact' for r in decision['requirements']):
        raise ValueError('A date alone cannot replace a required fact')
    return decision
