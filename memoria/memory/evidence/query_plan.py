"""Query-only, bounded evidence plan. Unknown identities/dates are never filled in."""
import re

VERSION = 'missing-requirements-plan-v3-evidence-chains'
FACETS = (
    ('project', r'哪个项目|哪些项目|什么项目|which project', '项目名称及这些记录属于同一项目的原话依据'),
    ('adoption', r'采用|选用|使用.*方案|adopt|chose|choose|chosen|approach', '原问题所询问的选择/采用行为、相关主体与对象的原话'),
    ('reason', r'为什么|为何|原因|理由|why|reason|rationale', '原问题询问的原因及原话支持，不补造因果'),
    ('outcome', r'后来|后续|遇到.*问题|困难|教训|问题.*后来|subsequent|later|challenge|problem|outcome', '原问题询问的后续事件、问题或结果及主体与时间关联'),
)


def complex_recall(query):
    return len(plan(query, {})['requirements']) >= 2


def plan(query, parsed):
    facts = [{'fact': key + ': ' + text, 'kind': 'fact'} for key, pattern, text in FACETS
             if re.search(pattern, query, re.I)]
    # All facets are frozen from the question before seeing any candidate. These
    # identify needed premises, not answers or inferred aliases/person identities.
    shared = re.search(r'\bboth\s+([A-Z][\w-]*)\s+and\s+([A-Z][\w-]*)\b', query)
    if shared:
        for subject in shared.groups():
            facts.append({'fact': 'subject_'+subject+': '+subject+
                '在原问题所问活动/属性上的独立原话证据，不能用另一人的经历代替', 'kind':'fact'})
        facts.append({'fact':'intersection: 两个已分别支持的主体记录具有原问题所问的同一对象/属性；保留同名消歧', 'kind':'fact'})
    if re.search(r'\bhow (?:long|many (?:days|weeks|months|years)) (?:did|has|had|have)\b'
                 r'|花了.*(?:多久|多长时间)|用了.*(?:多久|多长时间)|持续了|历时|多久.*(?:完成|开业|建成)',query,re.I):
        facts.extend([
            {'fact':'interval_start: 同一主体、同一事件的明确起点或直接陈述的持续时间，精度不得补造', 'kind':'fact'},
            {'fact':'interval_end: 同一事件的终点/完成状态或直接陈述的持续时间；计划不等于完成', 'kind':'fact'}])
    elif re.search(r'\bhow many (?:times|events|places|cities|countries)\b|多少次|几次|多少个',query,re.I):
        facts.extend([
            {'fact':'event_set: 原问题范围内可核验的独立事件及主体/日期，重复转述不算新事件；不以未召回证明未发生', 'kind':'fact'},
            {'fact':'event_count: 计数须由同一范围的已支持事件集合或直接原话支持，不能把多段同次经历重复计数', 'kind':'fact'}])
    elif re.search(r'\b(?:which|what) (?:events|activities|kinds|types|symbols|places)\b|哪些活动|哪些事件|哪些类型',query,re.I):
        facts.append({'fact':'collection: 收集原问题所问的互补实例及各自原话，不将一条相关记录误称为全部；区分本人行为与对方行为', 'kind':'fact'})
    facts = facts[:8]
    if not facts:
        facts = [{'fact': 'answer: 完整回答原问题所需的事实及主体、条件、否定和时间依据', 'kind': 'fact'}]
    return {'version': VERSION, 'origin': 'query_and_parser_only', 'question': query,
        'requirements': facts, 'subject_id': parsed.get('subject_id'),
        'context': parsed.get('context', {}), 'negation': bool(re.search(r'不|没有|未|\b(?:not|never)\b', query, re.I)),
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
