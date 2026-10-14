"""New ablations over frozen text sources; all decisions are query/evidence only."""
from __future__ import annotations

from collections import defaultdict
from ..memory.adaptive_fallback import AdaptiveFallback
from ..memory.answer_risk import AnswerRisk, SYSTEM as SUPPORT_SYSTEM, query_risk, is_refusal
from ..memory.evidence_sufficiency import EvidenceSufficiency, SYSTEM as SUFFICIENCY_SYSTEM
from ..memory.verification import verify, support_envelope
from ..llm.types import ContextLengthError, IncompleteResponseError
from ..prompting.reader_budget import ReaderBudget
from .history import canonical
from .locomo import parse_answer

PROTOCOL = 'locomo-sufficiency-risk-v3'
UNKNOWN = {'status': 'abstain', 'answer': 'Not mentioned.', 'evidence_ids': []}


async def run_case(client, settings, roles, question, history, anchors, chosen,
                   row, retrieve, *, method, reader_system, select_context):
    if method not in {'targeted', 'adaptive_risk'}:
        raise ValueError('Unknown risk ablation')
    policy = AdaptiveFallback(evidence_budgets=(chosen['budget_chars'], 16000, 20000) if method == 'adaptive_risk'
                              else (chosen['budget_chars'], 16000))
    risk = query_risk(question)
    row.update(query_risk=risk, primary_answer=None, primary_reader_called=False, primary_validation_error=None,
               primary_presented_ids=chosen['presented_ids'], fallback_attempted=False,
               risk_call_stages=[], sufficiency_checks=[], support_checks=[], adaptive_retrieval=[],
               policy_contract_errors=[])
    latest_usage, all_anchors = None, list(anchors)

    async def call(stage, system, packet):
        role = 'reader' if stage in {'reader', 'reader_repair'} else 'judge'
        cfg = roles[role]
        model = getattr(settings, cfg.slot)
        prepared = await ReaderBudget(settings).prepare(
            [{'role': 'system', 'content': system}, {'role': 'user', 'content': canonical(packet)}],
            selected=model, max_tokens=cfg.max_tokens, temperature=cfg.temperature, top_p=cfg.top_p)
        ticket = 'answer_pipeline' if method == 'adaptive_risk' and stage in {'reader','support'} else None
        if not policy.reserve_call(stage, prepared.report, reservation=ticket):
            return None
        row['risk_call_stages'].append({'call': len(client.calls), 'stage': stage, 'role': role,
                                       'reader_budget': prepared.report})
        row['adaptive_policy']=policy.public()
        return await client.call(role, prepared.messages, stream=False)

    async def assess(evidence):
        if not evidence:
            return {'state': 'unsupported', 'missing_queries': [question], 'reason': 'empty_packet',
                    'semantic_sufficiency_verified': False}
        try:
            decision = await verify(call, 'sufficiency', SUFFICIENCY_SYSTEM,
                {'question':question, 'evidence':evidence},
                lambda value: EvidenceSufficiency().validate(value,evidence), row['policy_contract_errors'])
        except IncompleteResponseError:
            raise  # A second length failure remains an auditable engineering failure.
        except ContextLengthError:
            decision = {'state':'unsupported','missing_queries':[], 'reason':'case_budget_exhausted'}
        except (ValueError, TypeError):
            decision = {'state': 'unsupported', 'missing_queries': [question],
                        'reason': 'assessor_contract_rejected', 'semantic_sufficiency_verified': False}
        row['sufficiency_checks'].append({'round': policy.retrieval_rounds, **decision})
        return decision

    async def read(evidence):
        nonlocal latest_usage
        result = await call('reader', reader_system, {'question': question, 'evidence': evidence})
        if result is None:
            return dict(UNKNOWN)
        latest_usage = result.usage or None
        try:
            parsed = parse_answer(result, [e['id'] for e in evidence])
            if parsed['status']=='answer' and is_refusal(parsed['answer']):
                raise ValueError('Refusal status contradicts answer text')
        except ValueError as exc:
            row['policy_contract_errors'].append({'stage': 'reader', 'error': type(exc).__name__})
            if policy.retrieval_rounds==0:
                row['primary_validation_error']='ReaderContractError'
            parsed = None
        if row['primary_answer'] is None and policy.retrieval_rounds==0:
            row.update(primary_answer=parsed or dict(UNKNOWN), primary_reader_called=True)
        if parsed is None:
            repair = await call('reader_repair', reader_system, {'question': question, 'evidence': evidence,
                'instruction': 'Regenerate valid JSON with exact presented citation IDs. Do not invent or force an answer.'})
            if repair is not None:
                latest_usage = repair.usage or None
                try:
                    parsed = parse_answer(repair, [e['id'] for e in evidence])
                    if parsed['status']=='answer' and is_refusal(parsed['answer']):
                        raise ValueError('Refusal status contradicts answer text')
                except ValueError as exc:
                    row['policy_contract_errors'].append({'stage': 'reader_repair', 'error': type(exc).__name__})
                    parsed = None
        return parsed or dict(UNKNOWN)

    async def support(answer, evidence):
        cited = [e for e in evidence if e['id'] in answer['evidence_ids']]
        try:
            decision = await verify(call, 'support', SUPPORT_SYSTEM,
                {'question':question, 'proposed_answer':answer['answer'], 'evidence':cited, 'query_risk':risk},
                lambda value: AnswerRisk().validate(value,question,answer,evidence), row['policy_contract_errors'])
        except IncompleteResponseError:
            raise
        except ContextLengthError:
            decision = {'accepted':False,'verdict':'insufficient','missing_queries':[], 'reason':'case_budget_exhausted'}
        except (ValueError, TypeError):
            decision = {'accepted': False, 'verdict': 'insufficient', 'missing_queries': [question],
                        'reason': 'support_contract_rejected', 'semantic_entailment_verified': False}
        row['support_checks'].append({'round': policy.retrieval_rounds, **decision})
        return decision

    async def reserve_answer_pipeline(evidence):
        cfg = roles['reader']
        meter = ReaderBudget(settings)
        prepared = await meter.prepare(
            [{'role':'system','content':reader_system},
             {'role':'user','content':canonical({'question':question,'evidence':evidence})}],
            selected=getattr(settings,cfg.slot), max_tokens=cfg.max_tokens,
            temperature=cfg.temperature, top_p=cfg.top_p)
        judge = roles['judge']
        envelope = await support_envelope(meter,SUPPORT_SYSTEM,
            {'question':question,'evidence':evidence,'query_risk':risk}, selected=getattr(settings,judge.slot),
            max_tokens=judge.max_tokens,temperature=judge.temperature,top_p=judge.top_p)
        return policy.hold_calls('answer_pipeline',[prepared.report,envelope,envelope])

    async def expand(decision):
        nonlocal all_anchors, chosen
        if policy.calls >= policy.max_calls or policy.input_bytes >= policy.max_input_bytes:
            policy.trace.append({'decision':'case_budget_exhausted'})
            return False
        plan = policy.plan(question, decision, risk)
        if not plan:
            return False
        lists, diagnostics = [all_anchors], []
        for query in plan['queries']:
            items, info = await retrieve(query, plan['candidate_limit'])
            lists.append(items)
            diagnostics.append({'query': query, **info})
        weights = defaultdict(float)
        lookup = {m['id']: m for m in history}
        positions = {m['id']: i for i, m in enumerate(history)}
        for items in lists:
            for rank, item in enumerate(items, 1):
                if item['id'] not in lookup or item != lookup[item['id']]:
                    raise ValueError('Supplement source differs from frozen conversation')
                weights[item['id']] += 1 / (60 + rank)
        retained=[lookup[mid] for mid in chosen['presented_ids']]
        retained_ids={m['id'] for m in retained}
        extra=[lookup[mid] for mid in sorted(weights,key=lambda mid:(-weights[mid],positions[mid])) if mid not in retained_ids][:80]
        all_anchors = retained+extra
        chosen = select_context(history, all_anchors, question=question, budget_chars=plan['evidence_chars'],
                                anchor_limit=len(all_anchors), radius=plan['context_radius'])
        if not retained_ids<=set(chosen['presented_ids']):
            raise ValueError('Supplement lost previously presented evidence')
        row['adaptive_retrieval'].append({'plan': plan, 'retrieval': diagnostics, 'selection': chosen})
        row.update(fallback_attempted=True, fallback_status='success')
        return True

    answer = dict(UNKNOWN)
    if method == 'targeted':
        answer = await read(chosen['payload']['evidence'])
        if answer['status'] != 'answer':
            planner = await assess(chosen['payload']['evidence'])
            # Ablation: use missing queries only, do not gate Reader or verify its answer.
            if await expand(planner):
                answer = await read(chosen['payload']['evidence'])
    else:
        while True:
            evidence = chosen['payload']['evidence']
            decision = await assess(evidence)
            if policy.retrieval_rounds==0 and decision['state']!='sufficient':
                row.update(primary_answer=dict(UNKNOWN),primary_reader_called=False,
                           primary_policy_abstained=True)
            if policy.retrieval_rounds>=1 and decision['state'] in {'unsupported','conflict'}:
                answer={**UNKNOWN,'status':'clarify' if decision['state']=='conflict' else 'abstain'}
                break
            if decision['state'] == 'sufficient':
                if not await reserve_answer_pipeline(evidence):
                    row['pipeline_budget_unavailable'] = True
                    break  # Do not spend Reader tokens on an answer that cannot be verified.
                try:
                    candidate = await read(evidence)
                    if candidate['status'] == 'answer':
                        decision = await support(candidate, evidence)
                        if decision['accepted']:
                            answer = candidate
                            break
                    else:
                        decision = {'state': 'missing_fact', 'missing_queries': [question], 'reason': 'reader_abstained'}
                finally:
                    policy.release('answer_pipeline')
                    row['adaptive_policy']=policy.public()
            if not await expand(decision):
                answer = {**UNKNOWN, 'status': 'clarify' if decision.get('state') == 'conflict' else 'abstain'}
                break
    row.update(status='success', answer_status=answer['status'], answer=answer['answer'],
               evidence_ids=answer['evidence_ids'], usage=latest_usage,
               retrieved_ids=[m['id'] for m in all_anchors], presented_ids=chosen['presented_ids'],
               selection=chosen, source_shas={m['id']: m['source_text_sha256'] for m in history
                                             if m['id'] in chosen['presented_ids']},
               adaptive_policy=policy.public(), semantic_entailment_verified=False)
