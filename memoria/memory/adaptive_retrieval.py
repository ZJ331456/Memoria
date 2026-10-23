"""Production missing-evidence loop: one bounded round, shared budget, closed supports.

Model advice never changes a Claim, identity, condition, source version or time axis.
"""
from __future__ import annotations
from ..observability.memory_trace import fingerprint

import re
import time
import asyncio
from copy import deepcopy

from ..claims import canonical, sha
from ..llm.types import ContextLengthError, ProviderError
from ..prompting import PromptAssembler, PromptSection
from ..prompting.reader_budget import ReaderBudget
from .access import QueryRequest
from .adaptive_fallback import AdaptiveFallback
from .answer_risk import AnswerRisk, SYSTEM as SUPPORT_SYSTEM
from .evidence.budget import EvidenceBudget
from .evidence.closure import select_closed
from .evidence.query_plan import plan, validate_plan
from .evidence_sufficiency import SYSTEM as GATE_SYSTEM
from .verification import verify, support_envelope, REPAIR_INSTRUCTION
from ..llm.call_scope import inference_scope

VERSION = 'production-missing-evidence-v4-actionable-support-contract'
SUPPORT_SYSTEM += ('\nCover EVERY literal character of the proposed answer, including prefixes such as '
    '根据来源记录 and final provenance caveats. Include a framing prefix in the adjacent factual statement '
    'rather than silently omitting it. A statement must be a literal substring, never a paraphrase. '
    'Do not invent an evidence quote to cover an unsupported statement; reject unsupported content.')
READER_STYLE = ('\n仅给出回答问题必需的事实，不添加“根据来源记录”等开场白或结尾元说明；'
    '来源与限制通过引用及已提供的元数据表达。只输出完整严格JSON，键名和值必须正确加引号。'
    '当系统提供required_evidence_ids时，回答若为answer，evidence_ids必须包含全部这些必要引用；不能删掉身份/时间/条件的支持。'
    'Use the same language as the question and a concise factual answer. Do not add administrative '
    'metadata narration (message timestamps, timezone unknown, citation explanations) unless requested. '
    'For relative dates use the source-local calendar only at its supported precision; a relative week '
    'must stay a week/range, never an invented exact day. Do not invent a missing acceptance or event. '
    'Distinguish the speaker\'s own actions from praise, questions or advice addressed to another person: '
    '"I admire your pottery" does not mean the speaker makes pottery. For multiple supported instances, '
    'include the complementary instances, not just the most recent passage. Never claim an exhaustive '
    'list or count if its required event set is not established.')
READER_REPAIR = '\n上一次输出JSON格式或长度不合要求。请从相同证据重新生成完整合法JSON；不要接续或修补上一段文本，不更改证据或引用。'


def reader_messages(messages, *, repair=False):
    result = deepcopy(messages)
    result[0]['content'] += READER_STYLE + (READER_REPAIR if repair else '')
    return result
POOL_CHARS, POOL_LIMIT = 16000, 16
GATE = (GATE_SYSTEM + '\nThe query_plan is frozen before evidence inspection. Assess EVERY entry of '
    'query_plan.requirements exactly once; copy its fact string and kind verbatim. Never delete, combine or '
    'replace a requirement just because evidence is missing. Quotes for project identity, adoption, reason and '
    'outcome must establish the SAME subject/project/episode; shared vocabulary alone is not identity. '
    'If the identity/time relation is unknown mark missing, never infer it from similarity. '
    'A missing query is only a search suggestion, not evidence. Source/identity/clock metadata are not new facts. '
    'The ONLY requirement entries are the frozen query_plan entries, all kind=fact. Attach temporal anchors as '
    'additional sources on that same fact, never add or rename requirements. Use field=session_date_raw ONLY '
    'when the evidence item has a TOP-LEVEL session_date_raw field. JSON keys inside text are not fields: '
    'quote them as field=text. A source_declared speaker is attribution within that source, not verified identity. '
    'Resolve relative calendar days using its stated raw session date without inventing a timezone; preserve '
    'date/month/year precision. A completed personal event can be established by that speaker\'s self-report. '
    'Do not require an independent biography when a source-attributed question asks what that speaker reported. '
    'Each evidence item declares quote_fields. Use ONLY those fields. A composite consolidation has no '
    'single session clock: do not cite its constituent clocks as its session_date_raw. Do not add irrelevant '
    'date quotes to a non-temporal question. For unavailable anchors use missing_fact and targeted retrieval. '
    'Match the question\'s requested precision: a generic "when" does NOT demand an exact day. '
    'An explicitly reported event "this month" with its same-source raw session clock can support that '
    'calendar month; preserve month precision, do not demand or invent a day. Likewise a year or week '
    'answer is sufficient when that is all the question requests and the event is established. '
    'Do not mark a requested object/type/value as supported by a related description that does not '
    'identify that object/type/value; suggest a targeted search for the missing attribute instead.')


class Personal:
    def __init__(self, kernel, request):
        self.kernel, self.request = kernel, request

    async def retrieve(self, query=None):
        if query is None:
            result = await self.kernel.access(self.request)
        else:
            result = await self.kernel.personal_access._access(self.request, retrieval_query=query)
            result = self.kernel.guard_pending_revisions(result, self.request)
        remap = {}
        for c in result['candidates']:
            old_id = c['id']
            data = c['data']
            if isinstance(data, dict):
                stable = {k: v for k, v in data.items() if k not in {'retrieval', 'unverified_retrieval_hint'}}
                stable['identity_dependency'] = data.get('retrieval', {}).get('entity_dependency')
                c['id'] += ':unit:' + sha(canonical(stable))[:16]
            remap[old_id] = c['id']
        # Selection objects are copies of candidates: their IDs must follow the same
        # source-window normalization, or every selected item looks falsely omitted.
        for selection in [result['selection'], *result.get('comparison', {}).values()]:
            for c in selection.get('payload', {}).get('evidence', []):
                c['id'] = remap.get(c['id'], c['id'])
            selection['candidate_ids'] = [remap.get(mid, mid) for mid in selection.get('candidate_ids', [])]
            for row in selection.get('trace', []) + selection.get('dropped', []):
                row['id'] = remap.get(row['id'], row['id'])
        if 'production_trace' in result:
            from ..observability.memory_trace import capture
            from .evidence.requirements import coverage
            original = result['production_trace']
            result['production_trace'] = capture(self.request.model_dump(mode='json'), result,
                self.kernel.claims.user_id, original['before'], original['after'],
                {c['id']: coverage(c, result['requirements'], result['parsed']) for c in result['candidates']})
        return result

    def present(self, access, candidates):
        from .reader import CitationGuard, ANSWER_CONTRACT, quoted_catalog
        value = {**access, 'selection': {**access['selection'], 'payload': {'evidence': candidates}}}
        guard = CitationGuard(self.kernel.claims, value, self.request,
            access_policy=lambda: bool(self.kernel.guard_pending_revisions(dict(value), self.request).get('pending_revisions')))
        messages = PromptAssembler().assemble(ANSWER_CONTRACT, extra_sections=[guard.section()]).as_messages()
        if required:=required_citations(access):
            messages[0]['content']+='\nrequired_evidence_ids='+canonical(required)
        messages.append({'role': 'user', 'content': self.request.query})
        owners = {}
        # Build each unit separately so a multi-Claim slot remains indivisible.
        for candidate in candidates:
            single = {**value, 'selection': {'payload': {'evidence': [candidate]}}}
            for eid in CitationGuard(self.kernel.claims, single, self.request).catalog:
                owners.setdefault(eid, []).append(candidate['id'])
        return guard, messages, quoted_catalog(guard), owners

    def budget(self):
        from .evidence.reader_tokenizer import for_reader
        r = self.request
        return EvidenceBudget(r.evidence_budget or r.budget_chars, r.budget_unit,
            for_reader(self.kernel.llm) if r.budget_unit == 'reader_tokens' else None)

    def final_access(self, access, selection):
        from ..observability.memory_trace import watermark, capture
        from .evidence.requirements import coverage
        value = {**access, 'selection': selection}
        point = watermark(self.kernel.store)
        value['production_trace'] = capture(self.request.model_dump(mode='json'), value,
            self.kernel.claims.user_id, point, point,
            {c['id']: coverage(c, access['requirements'], access['parsed']) for c in access['candidates']})
        return value


class SourceGuard:
    def __init__(self, kernel, request, packet):
        self.kernel, self.request, self.packet = kernel, request, packet

    def visible(self, messages):
        from ..prompting.assembler import _SECTION_BOUNDARY
        rendered = canonical(self.packet).replace(_SECTION_BOUNDARY, '&lt;!--memoria-section--&gt;')
        return any(rendered in m.get('content', '') for m in messages)

    def freshness(self):
        return ('source_changed' if (self.request.session_id and not self.kernel.store.session(self.request.session_id))
            or not all(self.kernel.dependency_valid(d, session_id=self.request.session_id)
                for d in self.packet['dependencies']) else None)

    def validate(self, result, messages):
        from .reader import ReaderAnswer, strict_json, SAFE_UNKNOWN, SAFE_REJECTED
        reason = self.freshness()
        answer = None
        if not self.visible(messages): reason = 'evidence_not_presented'
        elif result.finish_reason not in {None, 'stop'} or result.tool_calls: reason = 'incomplete_reader_output'
        else:
            try: answer = ReaderAnswer.model_validate(strict_json(result.content))
            except (ValueError, TypeError): reason = 'invalid_answer_schema'
        catalog = {e['citation']: e for e in self.packet['evidence']}
        if answer:
            ids = answer.evidence_ids
            inline = set(re.findall(r'\[(?:library|consolidation|memory|message):[^\]]+\]', answer.answer))
            if len(ids) != len(set(ids)) or set(ids) - set(catalog) or inline - set(ids) or (answer.status == 'answer' and not ids):
                reason = 'unseen_or_missing_source_citation'
        status = answer.status if answer and not reason else 'abstain'
        return {'status': status, 'answer': answer.answer if status == 'answer' else SAFE_UNKNOWN if not reason else SAFE_REJECTED,
            'citations': [{'id': eid, 'domain': catalog[eid]['domain'], 'source_id': catalog[eid]['source_id'],
                'provenance': catalog[eid]['provenance']} for eid in answer.evidence_ids] if status == 'answer' else [],
            'validation': {'valid': reason is None, 'reason': reason, 'semantic_entailment_verified': False}}


class Sources:
    def __init__(self, kernel, request):
        self.kernel, self.request = kernel, request

    async def retrieve(self, query=None):
        from .records import MemorySearch
        from .temporal_query import TemporalQueryParser
        captured = {}
        r = MemorySearch.model_validate(self.request.model_dump(exclude={'use_adaptive_retrieval', 'use_llm_rerank'}))
        parsed = TemporalQueryParser().parse(self.request.query)
        if parsed['range_start'] and not r.event_start and not r.recorded_as_of:
            if parsed['axis'] == 'fact':
                r = r.model_copy(update={'event_start': parsed['range_start'], 'event_end': parsed['range_end']})
            else:
                # Range-end knowledge reconstruction is not an exact past snapshot.
                return {'path': 'clarify', 'state': None, 'parsed': parsed, 'candidates': [],
                    'selection': {'payload': {'evidence': []}}}
        parsed.update(subject_id=r.subject_ref, fact_interval={'lower':r.event_start,'upper':r.event_end} if r.event_start else None,
            recorded_as_of=r.recorded_as_of)
        packet = await self.kernel.search(r, capture=captured, retrieval_query=query)
        candidates = []
        for i,(e,d) in enumerate(zip(captured['candidates'], captured['candidate_dependencies'])):
            e,d=source_packet_unit(e,d)
            candidates.append({'id':e['citation'],'kind':'library','score':1/(i+1),
                               'source_ids':[e['source_id']],'data':e,'dependency':d})
        ids = {e['citation'] for e in packet['evidence']}
        return {**packet, 'path': 'clarify' if packet['selection'].get('reason') == 'ambiguous_entity_identity' else 'retrieve',
                'retrieval': captured.get('retrieval', {}),
                'state': None, 'parsed': parsed, 'candidates': candidates,
                'selection': {**packet['selection'], 'payload': {'evidence': [c for c in candidates if c['id'] in ids]}}}

    def packet(self, candidates):
        return {'evidence': [c['data'] for c in candidates], 'dependencies': [c['dependency'] for c in candidates],
            'policy': 'Source-domain evidence, never approved personal state.'}

    def present(self, access, candidates):
        from .reader import SOURCE_CONTRACT
        packet = self.packet(candidates)
        if required:=required_citations(access):packet['required_evidence_ids']=required
        messages = PromptAssembler().assemble(SOURCE_CONTRACT,
            extra_sections=[PromptSection('Source Evidence', canonical(packet), 10, atomic=True)]).as_messages()
        messages.append({'role': 'user', 'content': self.request.query})
        messages[0]['content']+=('\nsource_unit的speaker/date是该片段原文的归属及日历声明，未独立验证身份。'
            '可用有原话支持的相对日历日期（如yesterday加该片段session_date_raw）回答日期问题；'
            '保留年月日精度和未知时区，不生成UTC时刻，不用source_recorded_at/upload时间推算事件。')
        evidence = []
        for c in candidates:
            e = c['data']
            if e['representation'] == 'consolidated_memory':
                quotes = [s[key] for s in e['provenance']['support'] for key in ('quote', 'subject_quote', 'feedback_quote') if s.get(key)]
            else:
                quotes = [e['content']]
            quotes.extend(q['quote'] for ident in e['provenance'].get('entity_identity', []) for q in ident['evidence'])
            entry={'id': c['id'], 'text': '\n'.join(quotes), 'source_texts': quotes}
            if unit:=e['provenance'].get('source_unit'):
                entry.update(speaker=unit['speaker'],session_date_raw=unit['session_date_raw'],
                             attribution='source_declared',timezone='unknown')
            entry['quote_fields']=['text']+(['session_date_raw'] if entry.get('session_date_raw') else [])
            evidence.append(entry)
        return SourceGuard(self.kernel, self.request, packet), messages, evidence, {c['id']: [c['id']] for c in candidates}

    def budget(self):
        return EvidenceBudget(self.request.budget_chars)

    def final_access(self, access, selection):
        packet = self.packet(selection['payload']['evidence'])
        if required:=required_citations(access):packet['required_evidence_ids']=required
        # Reader JSON/dependency overhead also belongs to the source evidence budget.
        if len(canonical(packet)) > self.request.budget_chars:
            raise ContextLengthError('Complete source packet exceeds evidence budget')
        from ..observability.memory_trace import record_read
        record_read(self.request.model_dump(mode='json'), [c['data'] for c in access['candidates']], packet['evidence'])
        return {**access, **packet, 'selection': selection}


def required_citations(access):
    return sorted({s['id'] for r in access.get('closure_advice',{}).get('decision',{}).get('requirements',[])
                   for s in r['sources']})


def source_packet_unit(record, dependency):
    """Raw evidence is stable across search queries; rankings/cards are retrieval hints.

    Formed memories have their own complete-support representation. Never inject
    query-selected auxiliary summaries as if they were part of this raw utterance.
    Temporal filtering already ran and its observation_refs dependencies are retained.
    """
    record,dependency=deepcopy(record),deepcopy(dependency)
    if record['representation']=='source_quote':
        provenance=record['provenance']
        provenance.pop('knowledge_cards',None)
        dependency.pop('card_ids',None)
        if provenance.get('source_unit'):
            provenance.pop('observations',None)
    return record,dependency


def merge_candidates(previous, new):
    """Same ID must still represent the same complete source unit."""
    catalog = {c['id']: c for c in previous}
    for c in new:
        def stable(candidate):
            data = candidate['data']
            return {k: v for k, v in data.items() if k not in {'retrieval', 'unverified_retrieval_hint'}} if isinstance(data, dict) else data
        if c['id'] in catalog and canonical(stable(catalog[c['id']])) != canonical(stable(c)):
            raise ValueError('Candidate changed between retrieval rounds')
        catalog.setdefault(c['id'], c)
    return list(catalog.values())


async def read_adaptive(kernel, request):
    from .reader import ReaderAnswer, strict_json, SAFE_UNKNOWN, SAFE_REJECTED
    started = time.perf_counter()
    adapter = Personal(kernel, request) if isinstance(request, QueryRequest) else Sources(kernel, request)
    policy = AdaptiveFallback(evidence_budgets=(6000, 6000), max_calls=8, max_input_bytes=240000)
    meter = getattr(kernel.llm, 'reader_budget', None) or ReaderBudget(kernel.settings)
    trace = {'version': VERSION, 'enabled': True, 'max_supplement_rounds': 1, 'retrieval_rounds': 0,
        'rounds': [], 'calls': [], 'contract_errors': [], 'unknown_usage_count': 0,
        'full_history_reader_allowed': False, 'llm_rerank_enabled': False, 'semantic_sufficiency_verified': False}
    access = {'adaptive': trace, 'candidates': []}
    reader_called = False
    reader_usage = None

    def finish(reason, *, status='abstain', verdict=None):
        policy.release('answer_pipeline')
        trace['policy'] = policy.public()
        trace['unknown_usage_count'] = sum(c['usage'] is None for c in trace['calls'])
        trace['elapsed_ms'] = round((time.perf_counter() - started) * 1000, 3)
        return {**(verdict or {'status': status, 'answer': SAFE_UNKNOWN if status == 'clarify' else SAFE_REJECTED,
            'citations': [], 'validation': {'valid': False, 'reason': reason, 'semantic_entailment_verified': False}}),
            'reader_called': reader_called, 'usage': reader_usage, 'access': access,
            'reader_budget': next((c['request_budget'] for c in reversed(trace['calls']) if c['stage'] == 'reader'), None),
            'risk_checks': {'mode': 'missing_requirement_loop', 'usage': [c['usage'] for i,c in enumerate(trace['calls'])
                if i != max((j for j,c in enumerate(trace['calls']) if c['stage']=='reader'),default=-1)],
                'adaptive_retrieval_enabled': True, 'policy': policy.public()}}

    def observe_embedding(payload):
        size = len(canonical(payload).encode('utf-8'))
        report = {'input_bytes': size, 'fits': size <= kernel.settings.reader_input_bytes,
            'scope': 'complete_embedding_request_json', 'exact_input_tokens': False}
        if not report['fits'] or not policy.reserve_call('embedding', report):
            raise ContextLengthError('Shared embedding/Reader budget exhausted')
        row = {'stage': 'embedding', 'status': 'started', 'usage': None, 'request_budget': report}
        trace['calls'].append(row)
        from .reader_calls import record_call
        record_call(row)
        return row

    async def retrieve(query=None):
        async with asyncio.timeout(min(kernel.settings.request_timeout_seconds, 90)):
            with inference_scope(observe_embedding):
                return await adapter.retrieve(query)

    async def call(stage, system, packet):
        from ..llm.deepseek_cache import evidence_messages, official, layout_of
        # Preserve the established fallback for the single contract repair. New layouts
        # cannot silently replace it in an attempt to increase cache hits.
        prepared = await meter.prepare(evidence_messages(kernel.settings.main,system,packet,
                enabled=REPAIR_INSTRUCTION not in system),
                                       max_tokens=2048,atomic=True)
        if not policy.reserve_call(stage, prepared.report, reservation='answer_pipeline' if stage == 'support' else None):
            raise ContextLengthError('Shared adaptive call/input budget exhausted')
        row = {'stage': stage, 'prompt_sha256': fingerprint(prepared.messages), 'prompt_chars': len(canonical(prepared.messages)), 'request_budget': prepared.report, 'usage': None, 'status': 'started'}
        trace['calls'].append(row)
        row['cache_provider']='deepseek' if official(prepared.model) else None
        row['cache_layout']=layout_of(prepared.messages) if official(prepared.model) else None
        if row['cache_layout']:
            from ..llm.deepseek_cache import cache_epoch
            # A hit-rate drop must be attributable, not guessed: model, layout, tool contract,
            # truncation and stage are folded into one tag that is recorded next to the call.
            row['cache_epoch']=cache_epoch(model=getattr(prepared.model,'model',''),
                endpoint=getattr(prepared.model,'base_url',''),layout=row['cache_layout'],
                truncated=bool((prepared.report or {}).get('truncated_messages')),mode=stage)
        from .reader_calls import record_call
        record_call(row)
        clock = time.perf_counter()
        try:
            async with asyncio.timeout(min(kernel.settings.request_timeout_seconds, 90)):
                with inference_scope(observe_embedding):
                    from .reader_calls import notify_request
                    notify_request()
                    result = await kernel.llm.chat(prepared.messages, model=prepared.model, max_tokens=prepared.max_tokens)
            row.update(raw=result.content, usage=result.usage or None, retries=getattr(result,'retries',None), finish_reason=result.finish_reason, status='returned')
            return result
        except BaseException as exc:
            row.update(status='failed', error_type=type(exc).__name__)
            raise
        finally:
            row['elapsed_ms'] = round((time.perf_counter() - clock) * 1000, 3)
            if row['usage'] is None: trace['unknown_usage_count'] += 1

    try:
        from .auto_review import SECRET
        if SECRET.search(request.query): return finish('credential_bearing_query_not_sent')
        access = await retrieve()
        access['adaptive'] = trace
        trace['initial_selection_ids'] = [c['id'] for c in access['selection']['payload']['evidence']]
        trace['initial_candidates'] = deepcopy(access['candidates'])
        query_plan = plan(request.query, access.get('parsed', {}))
        trace['query_plan'] = query_plan
        authority_block = (access.get('parsed', {}).get('requires_clarification') or access.get('pending_revisions')
            or (access.get('state') and not access['state']['payload']['complete']))
        if access['path'] == 'clarify' and authority_block:
            return finish('authority_requires_clarification', status='clarify')
        if access['path'] == 'clarify': access['path'] = 'retrieve'
        if isinstance(request, QueryRequest) and (access.get('state') or access['path'] == 'history'):
            # These authority paths already have indivisible slot/version groups.
            # A source supplement cannot repair an incomplete/unapproved Claim state.
            if access.get('state') and not access['state']['payload']['complete']:
                return finish('incomplete_approved_state', status='clarify')
        all_candidates = access['candidates']
        for round_index in range(2):
            # Retain complete original candidates/ranks; only the model packet is bounded.
            pool, omitted = [], []
            for c in all_candidates:
                _, _, packet, _ = adapter.present(access, pool + [c])
                if len(pool) < POOL_LIMIT and len(canonical(packet)) <= POOL_CHARS:
                    pool.append(c)
                else: omitted.append(c['id'])
            if not pool and (round_index or all_candidates): return finish('no_bounded_live_candidates')
            guard, _, evidence, owners = adapter.present(access, pool)
            if guard.freshness(): return finish('source_changed_before_gate')
            # Reserve complete Reader plus two support attempts BEFORE spending on gates.
            # Whole pool is a conservative envelope, never clipped to pretend it fits.
            _, future_messages, _, _ = adapter.present(access, pool)
            future = await meter.prepare(reader_messages(future_messages))
            future_repair = await meter.prepare(reader_messages(future_messages, repair=True))
            support_bound = await support_envelope(meter, SUPPORT_SYSTEM, {'question': request.query, 'evidence': evidence},deepseek_cache=True)
            policy.release('answer_pipeline')
            if not policy.hold_calls('answer_pipeline', [future.report, future_repair.report, support_bound, support_bound]):
                return finish('complete_answer_reservation_unavailable')
            payload = {'question': request.query, 'query_plan': query_plan, 'evidence': evidence}
            if SECRET.search(canonical(payload) + canonical(future_messages)): return finish('credential_bearing_source_not_sent')
            if pool:
                decision = await verify(call, 'coverage', GATE, payload,
                    lambda value: validate_plan(value, evidence, query_plan), trace['contract_errors'])
            else:
                # Empty initial recall is a retrieval gap, not a negative semantic judgment.
                decision = {'state': 'missing_fact', 'requirements': [{**r, 'status': 'missing', 'sources': []}
                    for r in query_plan['requirements']], 'missing_queries': [], 'reason': 'empty_initial_pool',
                    'judgment_source': 'query_only_empty_pool', 'semantic_sufficiency_verified': False}
            selected = select_closed(pool, decision, owners, adapter.budget(), limit=getattr(request, 'top_k', request.limit if hasattr(request, 'limit') else 8))
            required_ids = {cid for group in selected['groups'] for cid in group['candidate_ids']}
            record = {'round': round_index, 'candidate_ids': [c['id'] for c in all_candidates],
                'assessed_candidate_ids': [c['id'] for c in pool], 'omitted_from_gate': omitted,
                'decision': decision, 'selection': selected,
                'citation_owners': owners,
                'initially_recalled_but_not_selected': sorted(required_ids - set(trace['initial_selection_ids'])),
                'not_supported_in_assessed_pool': [r['fact'] for r in decision['requirements'] if r['status'] == 'missing']}
            trace['rounds'].append(record)
            if guard.freshness(): return finish('source_changed_during_gate')
            if decision['state'] == 'conflict': return finish('evidence_conflict', status='clarify')
            if decision['state'] == 'sufficient':
                if not selected['complete']: return finish('necessary_group_over_budget')
                access = adapter.final_access({**access, 'candidates': all_candidates,
                    'closure_advice': {'decision': decision, 'citation_owners': owners,
                        'candidate_ids': [c['id'] for c in pool]}}, selected)
                access['adaptive'] = trace
                guard, messages, evidence, owners = adapter.present(access, selected['payload']['evidence'])
                # The final packet must contain EVERY verified necessary quote.
                validate_plan({k: decision[k] for k in ('state', 'requirements', 'missing_queries', 'reason')}, evidence, query_plan)
                prepared = await meter.prepare(reader_messages(messages))
                repaired = await meter.prepare(reader_messages(messages, repair=True))
                if not guard.visible(prepared.messages) or guard.freshness(): return finish('final_evidence_changed')
                # Replace the conservative hold with the actual, complete final pipeline.
                final_support = await support_envelope(meter, SUPPORT_SYSTEM, {'question': request.query, 'evidence': evidence},deepseek_cache=True)
                policy.release('answer_pipeline')
                if not policy.hold_calls('answer_pipeline', [prepared.report, repaired.report, final_support, final_support]):
                    return finish('final_answer_reservation_unavailable')
                for attempt in range(2):
                    if attempt: prepared = repaired
                    if guard.freshness(): return finish('source_changed_before_reader')
                    policy.reserve_call('reader', prepared.report, reservation='answer_pipeline')
                    reader_called = True
                    clock = time.perf_counter()
                    row = {'stage': 'reader', 'prompt_sha256': fingerprint(prepared.messages), 'prompt_chars': len(canonical(prepared.messages)), 'attempt': attempt+1, 'request_budget': prepared.report, 'usage': None, 'status': 'started'}
                    from ..llm.deepseek_cache import official
                    row['cache_provider']='deepseek' if official(prepared.model) else None
                    trace['calls'].append(row)
                    from .reader_calls import record_call
                    record_call(row)
                    try:
                        async with asyncio.timeout(min(kernel.settings.request_timeout_seconds, 90)):
                            with inference_scope(observe_embedding):
                                from .reader_calls import notify_request
                                notify_request()
                                result = await kernel.llm.chat(prepared.messages, model=prepared.model, max_tokens=prepared.max_tokens)
                        reader_usage = result.usage or None
                        row.update(raw=result.content, usage=reader_usage, retries=getattr(result,'retries',None), finish_reason=result.finish_reason, status='returned')
                    except BaseException as exc:
                        row.update(status='failed', error_type=type(exc).__name__)
                        raise
                    finally:
                        row['elapsed_ms'] = round((time.perf_counter() - clock) * 1000, 3)
                    verdict = guard.validate(result, prepared.messages)
                    repairable = (not result.tool_calls and result.finish_reason in {None,'stop','length'}
                        and verdict['validation']['reason'] in {'invalid_answer_schema','incomplete_reader_output'})
                    if not repairable: break
                    trace['contract_errors'].append({'stage':'reader','attempt':attempt+1,'code':verdict['validation']['reason']})
                if verdict['status'] != 'answer': return finish(None, verdict=verdict)
                policy.release('answer_pipeline')
                if not policy.hold_calls('answer_pipeline', [final_support, final_support]):
                    return finish('support_reservation_unavailable')
                answer = ReaderAnswer.model_validate(strict_json(result.content))
                from .answer_risk import is_refusal
                if is_refusal(answer.answer): return finish('refusal_status_conflict')
                # Citing only a subset cannot drop a necessary name/fact/condition dependency.
                required_citations = {q['id'] for r in decision['requirements'] for q in r['sources']}
                if not required_citations <= set(answer.evidence_ids): return finish('necessary_citations_omitted')
                check = await verify(call, 'support', SUPPORT_SYSTEM,
                    {'question': request.query, 'proposed_answer': answer.answer,
                     'evidence': [e for e in evidence if e['id'] in answer.evidence_ids]},
                    lambda value: AnswerRisk().validate(value, request.query, answer.model_dump(), evidence), trace['contract_errors'])
                # Keep the dependency proof, not a second copy of the answer and
                # quoted personal/source text. Original verification remains in its
                # single call receipt and the private experiment ledger.
                trace['answer_support'] = {k:check[k] for k in ('version','accepted','query_risk',
                    'support_dependencies','rule_blockers','quote_integrity_verified',
                    'semantic_entailment_verified','judgment_source')}
                if not check['accepted']: return finish('answer_support_not_established')
                if guard.freshness(): return finish('source_changed_before_publish')
                verdict = guard.validate(result, prepared.messages)
                return finish(None, verdict=verdict)
            if round_index or decision['state'] != 'missing_fact':
                return finish('required_evidence_not_established')
            missing = record['not_supported_in_assessed_pool']
            # A single supplemental ROUND, at most two queries, same immutable access request.
            queries = decision['missing_queries'][:2] or missing[:2]
            if not queries: return finish('no_targeted_requirement')
            trace['retrieval_rounds'] = 1
            policy.retrieval_rounds = 1
            for query in queries:
                # Only the locator changes; original identity/time/conditions still govern _access/search.
                target = query.strip()
                supplement = await retrieve(target)
                trace.setdefault('supplements', []).append({'query': target,
                    'candidate_ids': [c['id'] for c in supplement['candidates']], 'candidates': deepcopy(supplement['candidates']),
                    'retrieval': supplement.get('retrieval'),
                    'rejected': supplement.get('rejected', []), 'timings': supplement.get('timings')})
                if supplement['path'] == 'clarify': return finish('authority_changed_during_supplement', status='clarify')
                all_candidates = merge_candidates(all_candidates, supplement['candidates'])
                access['candidates'] = all_candidates
            # Keep known necessary units and new arrivals ahead of unneeded old candidates.
            new_ids = {cid for s in trace['supplements'] for cid in s['candidate_ids']}
            all_candidates.sort(key=lambda c: (c['id'] not in required_ids, c['id'] not in new_ids))
        return finish('supplement_round_limit')
    except (ValueError, TypeError, KeyError, ProviderError, RuntimeError, TimeoutError) as exc:
        trace['error_type'] = type(exc).__name__
        return finish('adaptive_budget_or_contract_failure')
