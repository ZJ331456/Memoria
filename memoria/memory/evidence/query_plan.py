"""Question-faithful bounded requirements; retrieval hints never become evidence."""
import re

VERSION = 'missing-requirements-plan-v4-question-spans'
FACETS = (
    ('project', r'哪个项目|哪些项目|什么项目|which project', '项目名称及这些记录属于同一项目的原话依据'),
    ('adoption', r'采用|选用|使用[^？?。;；]*方案|\b(?:adopt(?:ed|s|ing)?|chos(?:e|en)|choos(?:e|es|ing))\b', '原问题所询问的选择/采用行为、相关主体与对象的原话'),
    ('reason', r'为什么|为何|原因|理由|\b(?:why|reason|rationale)\b', '原问题询问的原因及原话支持，不补造因果'),
    ('change', r'(?:改变|修改|调整)了什么|什么(?:改变|修改|调整)|\bwhat\b[^?？;；]*\b(?:change|changed|modify|modified|adjust|adjusted)\b', '原问题询问的具体修改或变化、原主体与事件关联；不把后续时间本身当成变化'),
    ('resolution', r'(?:解决|改善)了什么|(?:哪个|什么|哪些)问题[^？?。;；]*(?:解决|改善)|\b(?:which|what)\b[^?？;；]*\b(?:solve|solved|resolve|resolved|fix|fixed)\b', '原问题询问的问题与解决行为之间的明确关系，不以先后顺序补造因果'),
    ('outcome', r'后来|后续|遇到[^？?。;；]*问题|困难|教训|\b(?:challenge|problem|outcome|result)\b|\bwhat\b[^?？;；]*\b(?:happened|afterward|afterwards)\b', '原问题询问的后续事件、问题或结果及主体与时间关联'),
)
_SPLIT = re.compile(r'[?？;；。]+|(?:,?\s*\b(?:and|also|then)\s+|,\s*)(?=(?:what|which|who|where|when|why|how)\b)|[，,、]?(?:以及|并且|然后)(?=(?:什么|哪些|哪个|为何|为什么|何时|哪里|后来))', re.I)
_QUESTION = re.compile(r'\b(?:what|which|who|where|when|why|how)\b|什么|哪些|哪个|为何|为什么|何时|哪里|多久|多少|几次', re.I)
_STOP = frozenset('what which who where when why how did does do has have had is are was were the a an and or to of in on at for it they he she i you we their his her my our its that this with from'.split())


def literal_terms(text):
    """Lexical retrieval features, never a judgment of semantic sufficiency."""
    values = re.findall(r'[A-Za-z][\w+-]*|[\u4e00-\u9fff]+', text)
    terms = set(t for value in values for t in (
        [value.casefold()] if value.isascii() else [value[i:i+2] for i in range(len(value)-1)])
        if len(t) > 1 and t not in _STOP)
    # Bounded surface variants let "change" inspect "changed" and "visit"
    # inspect "visits" without inventing semantic synonyms or event relations.
    for value in list(terms):
        if not value.isascii(): continue
        if value.endswith('ed') and len(value)>4: terms.update((value[:-2], value[:-1]))
        elif value.endswith('ing') and len(value)>5: terms.update((value[:-3], value[:-3]+'e'))
        elif value.endswith('s') and len(value)>3: terms.add(value[:-1])
    return terms


def _spans(query):
    result, start = [], 0
    for boundary in _SPLIT.finditer(query):
        end = boundary.start()
        if query[start:end].strip(): result.append((start, end))
        start = boundary.end()
    if query[start:].strip(): result.append((start, len(query)))
    return result or [(0, len(query))]


def complex_recall(query):
    return len(plan(query, {})['requirements']) >= 2


def plan(query, parsed):
    spans = _spans(query)
    facts, specs, covered = [], [], set()
    overflow = False
    # Literal name-like words help retrieval, but do not resolve any identity.
    anchors = list(dict.fromkeys(t for t in re.findall(r'\b[A-Z][\w-]*\b', query)
                               if t.casefold() not in _STOP))[:6]

    def add(key, description, span, *, subject=None):
        nonlocal overflow
        if len(facts) >= 8:
            overflow = True
            return
        fact = key + ': ' + description
        facts.append({'fact': fact, 'kind': 'fact'})
        start, end = span
        clause = query[start:end].strip(' ,，')
        locator = ' '.join([*(anchors if not subject else [subject]), clause])
        specs.append({'id': key, 'fact': fact, 'question_span': [start, end],
            'question_text': query[start:end], 'locator': locator[:300],
            'lexical_terms': sorted(literal_terms(clause))})

    for key, pattern, description in FACETS:
        matches = [i for i, (start, end) in enumerate(spans) if re.search(pattern, query[start:end], re.I)]
        if not matches: continue
        span_index = matches[0]
        # A question about a resolved problem does not separately ask for an
        # unspecified later event. Likewise a later change needs only its change.
        if key == 'outcome':
            independent = [i for i in matches if not any(s['id'] in {'change','resolution'} and
                s['question_span']==list(spans[i]) for s in specs)]
            if not independent: continue
            span_index = independent[0]
        add(key, description, spans[span_index]); covered.add(span_index)
    # All facets are frozen from the question before seeing any candidate. These
    # identify needed premises, not answers or inferred aliases/person identities.
    shared = re.search(r'\b(?i:both)\s+([A-Z][\w-]*)\s+and\s+([A-Z][\w-]*)\b', query)
    if shared:
        shared_span = next(span for span in spans if span[0] <= shared.start() < span[1])
        for subject in shared.groups():
            add('subject_'+subject, subject+'在原问题所问活动/属性上的独立原话证据，不能用另一人的经历代替',
                shared_span, subject=subject)
        add('intersection', '两个已分别支持的主体记录具有原问题所问的同一对象/属性；保留同名消歧', shared_span)
        covered.add(spans.index(shared_span))
    structured = (
        (r'\bhow (?:long|many (?:days|weeks|months|years)) (?:did|has|had|have)\b'
         r'|花了.*(?:多久|多长时间)|用了.*(?:多久|多长时间)|持续了|历时|多久.*(?:完成|开业|建成)', [
            ('interval_start', '同一主体、同一事件的明确起点或直接陈述的持续时间，精度不得补造'),
            ('interval_end', '同一事件的终点/完成状态或直接陈述的持续时间；计划不等于完成')]),
        (r'\bhow many (?:times|events|places|cities|countries)\b|多少次|几次|多少个', [
            ('event_set', '原问题范围内可核验的独立事件及主体/日期，重复转述不算新事件；不以未召回证明未发生'),
            ('event_count', '计数须由同一范围的已支持事件集合或直接原话支持，不能把多段同次经历重复计数')]),
        (r'\b(?:which|what) (?:events|activities|kinds|types|symbols|places)\b|哪些活动|哪些事件|哪些类型', [
            ('collection', '收集原问题所问的互补实例及各自原话，不将一条相关记录误称为全部；区分本人行为与对方行为')]),
    )
    for pattern, entries in structured:
        matching = [i for i, (start,end) in enumerate(spans) if re.search(pattern,query[start:end],re.I)]
        if matching:
            index = matching[0]
            for key, description in entries: add(key, description, spans[index])
            # Other independent subquestions must still become frozen units.
            covered.add(index)
    for i, span in enumerate(spans):
        if i not in covered and _QUESTION.search(query[span[0]:span[1]]) and (len(spans) > 1 or not facts):
            add('detail_'+str(i+1), '回答原问题子句“'+query[span[0]:span[1]].strip()[:220]+'”所需的主体、事实与条件依据', span)
    if not facts:
        add('answer', '完整回答原问题所需的事实及主体、条件、否定和时间依据', (0, len(query)))
    # The eight-unit model contract cannot justify dropping later subquestions.
    if len(facts) == 8 and (overflow or any(i not in covered and not any(s['question_span'] == list(span) for s in specs)
            for i, span in enumerate(spans))):
        facts[-1] = {'fact': 'remaining: 原问题中其余全部子句及其主体、条件、否定与时间依据', 'kind': 'fact'}
        specs[-1] = {'id':'remaining','fact':facts[-1]['fact'],'question_span':[0,len(query)],
            'question_text':query,'locator':query[:300], 'lexical_terms':sorted(literal_terms(query))}
    return {'version': VERSION, 'origin': 'query_and_parser_only', 'question': query,
        'requirements': facts, 'requirement_specs': specs, 'subject_id': parsed.get('subject_id'),
        'context': parsed.get('context', {}), 'negation': bool(re.search(r'不|没有|未|\b(?:not|never)\b', query, re.I)),
        'fact_interval': parsed.get('fact_interval'), 'recorded_interval': parsed.get('recorded_interval'),
        'time_precision': parsed.get('time_precision', 'unknown'),
        'unknowns_preserved': True, 'gold_used': False}


def supplemental_targets(query_plan, decision):
    """Bind at most two locators to explicitly missing frozen requirements."""
    missing = {r['fact'] for r in decision['requirements'] if r['status'] == 'missing'}
    specs = [s for s in query_plan['requirement_specs'] if s['fact'] in missing]
    suggestions = decision.get('missing_queries', [])
    targets = []
    for spec in specs:
        terms = set(spec['lexical_terms'])
        suggestion = next((q.strip() for q in suggestions if literal_terms(q) & terms), None)
        # Advice augments the literal locator; it never replaces original scope.
        query = spec['locator'] if not suggestion else (spec['locator'][:200]+' '+suggestion[:99]).strip()
        if query not in {t['query'] for t in targets}:
            targets.append({'requirement':spec['fact'],'query':query[:300],
                'basis':'question_locator_with_advice' if suggestion else 'question_locator'})
        if len(targets) == 2: break
    return targets


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
