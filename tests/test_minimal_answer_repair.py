"""Synthetic fault injection for deletion-only repair; no quality or paid calls."""
import asyncio
import json

import pytest

from memoria.llm.types import ChatResult
from memoria.memory.access import QueryRequest
from memoria.memory.answer_repair import INSTRUCTION, retained_units, validate_deletion
from memoria.memory.answer_risk import AnswerRisk, SYSTEM as SUPPORT
from memoria.memory.adaptive_retrieval import GATE
from memoria.memory.reader import ReaderAnswer
from memoria.prompting.assembler import _parse_frame
from tests.test_adaptive_retrieval import core, QUERY, TEXT

__all__ = ['core']
EXTRA = '另有一艘游艇。'


def mock_repair(core, *, changed=False, reject=False, withdraw=False, malformed=False, unknown=False, partial=False):
    kernel, model, message = core
    original = model.chat
    counts = {'reader':0, 'support':0, 'repair':0}

    async def chat(messages, **kwargs):
        system = messages[0]['content']
        if GATE in system:
            return await original(messages, **kwargs)
        model.calls.append(system)
        if SUPPORT in system:
            counts['support'] += 1
            packet = json.loads(messages[1]['content'])
            evidence = packet['evidence'][0]
            claims = [{'statement':TEXT, 'relation':'insufficient' if reject and counts['support']>1 else 'supported',
                'basis':'explicit', 'sources':[{'id':evidence['id'], 'quote':evidence['source_texts'][0]}], 'reason':'Synthetic.'}]
            if EXTRA in packet['proposed_answer']:
                claims.append({'statement':EXTRA, 'relation':'insufficient', 'basis':'inference', 'sources':[], 'reason':'No evidence.'})
            value = {'verdict':'supported' if all(c['relation']=='supported' for c in claims) else 'insufficient',
                'claims':claims, 'missing_queries':[], 'reason':'Synthetic independent check.'}
            if 'query_plan' in packet:
                value['requirement_coverage']=[{'fact':r['fact'],
                    'status':'missing' if (partial or reject) and counts['support']>1 else 'answered',
                    'answer_units':[] if (partial or reject) and counts['support']>1 else [TEXT]}
                    for r in packet['query_plan']['requirements']]
        else:
            _, sections = _parse_frame(messages[1]['content'])
            packet = json.loads(next(s for s in sections if s.name=='Structured State').content.rsplit('\n',1)[-1])
            ids = packet['allowed_evidence_ids']
            counts['reader'] += 1
            repaired = INSTRUCTION in system
            if repaired:
                counts['repair'] += 1
                assert '\nretained_answer_units='+json.dumps([TEXT], ensure_ascii=False, separators=(',',':')) in system
                assert EXTRA not in system
                if withdraw:kernel.store.undo_memory_sources([message['id']])
                if malformed:return ChatResult(content='{broken', finish_reason='stop', usage={'total_tokens':7})
            answer = TEXT + ('不是。' if changed else '') if repaired else TEXT + EXTRA
            value = {'status':'answer', 'answer':answer, 'evidence_ids':ids}
        return ChatResult(content=json.dumps(value, ensure_ascii=False), finish_reason='stop',
            usage=None if unknown and INSTRUCTION in system else {'total_tokens':7})

    kernel.llm.chat = chat
    return kernel, model, counts


def run(kernel):
    return asyncio.run(kernel.answer(QueryRequest(query=QUERY, use_adaptive_retrieval=True,
        include_experiences=False, budget_chars=16000)))


def test_minimal_repair_preserves_supported_answer_then_independently_rechecks(core):
    kernel, model, counts = mock_repair(core)
    result = run(kernel)
    assert result['status']=='answer' and result['answer']==TEXT, result
    trace = result['access']['adaptive']
    assert counts=={'reader':2, 'support':2, 'repair':1}
    assert [c['stage'] for c in trace['calls']]==['coverage','reader','support','answer_repair','support']
    assert not trace['answer_support']['accepted'] and trace['answer_support_after_repair']['accepted']
    assert trace['answer_repair']['accepted'] and trace['answer_repair']['same_evidence']
    assert trace['answer_support_after_repair']['question_requirement_coverage_verified']
    assert trace['policy']['max_calls']==8 and trace['policy']['calls']==5 and trace['policy']['reserved_calls']==0
    assert trace['policy']['input_bytes']<=240000
    assert sum((u or {}).get('total_tokens',0) for u in result['risk_checks']['usage'])+result['usage']['total_tokens']==sum(c['usage']['total_tokens'] for c in trace['calls'])


def test_repair_support_reservation_includes_full_query_plan_for_deepseek_layout(core):
    from memoria.llm.deepseek_cache import COMMON_SYSTEM, evidence_payload
    kernel, _, _ = mock_repair(core)
    kernel.settings.main.base_url='https://api.deepseek.com/v1'
    kernel.settings.main.api_key='synthetic-test-only'
    original=kernel.llm.chat
    wire=[]

    async def decode(messages, **kwargs):
        wire.append(messages)
        if messages[0]['content']==COMMON_SYSTEM:
            packet={**evidence_payload(messages[1]), **json.loads(messages[-1]['content'])}
            packet.pop('source_id_allowlist')
            messages=[{'role':'system','content':messages[2]['content']},
                      {'role':'user','content':json.dumps(packet, ensure_ascii=False)}]
        return await original(messages, **kwargs)

    kernel.llm.chat=decode
    result=run(kernel)
    trace=result['access']['adaptive']
    assert result['status']=='answer', result['validation']
    assert trace['answer_support_after_repair']['question_requirement_coverage_verified']
    assert trace['policy']['calls']==len(wire)==5
    # reserve_call rejects even a one-byte excess. Successful real packet
    # preparation proves the query_plan-containing support fits its hold.
    assert trace['policy']['reserved_calls']==0


@pytest.mark.parametrize('issue', ['changed','reject','withdraw','malformed','partial'])
def test_repair_never_adds_facts_weakens_support_or_retries_again(core, issue):
    kernel, _, counts = mock_repair(core, **{issue:True})
    result = run(kernel)
    if issue in {'reject', 'partial', 'changed'}:
        # Publish-with-marker policy: a cited, gate-sufficient Reader answer is
        # not discarded because the independent support check or the deletion
        # contract stayed negative; the ORIGINAL answer is published, marked.
        assert result['status']=='answer' and result['citations']
        assert result['validation']['support_rejected_published'] is True
        assert result['access']['adaptive']['support_override']['published'] is True
    else:
        assert result['status']=='abstain' and not result['citations']
    assert counts['repair']==1 and counts['support']==(2 if issue in {'reject','partial'} else 1)
    assert result['access']['adaptive']['policy']['reserved_calls']==0


def test_repair_failed_usage_is_retained_as_unknown(core):
    kernel, _, _ = mock_repair(core, unknown=True)
    result = run(kernel)
    trace=result['access']['adaptive']
    assert result['status']=='answer' and result['usage'] is None
    assert trace['unknown_usage_count']==1
    assert trace['calls'][3]['stage']=='answer_repair' and trace['calls'][3]['usage'] is None


def test_format_repairs_and_supplement_exhaust_shared_slots_before_optional_answer_repair(core):
    kernel, model, counts = mock_repair(core)
    original=kernel.llm.chat
    model.missing=2  # First gate plus its contract repair remain negative.
    injected={'gate':False, 'reader':False, 'support':False}

    async def faults(messages, **kwargs):
        system=messages[0]['content']
        if GATE in system:
            result=await original(messages, **kwargs)
            if not injected['gate']:
                injected['gate']=True
                value=json.loads(result.content)
                value['requirements'][0]['sources'][0]['quote']='Invented quote is not in source'
                result.content=json.dumps(value)
            return result
        stage='support' if SUPPORT in system else 'reader'
        if not injected[stage]:
            injected[stage]=True
            model.calls.append(system)
            return ChatResult(content='{broken', finish_reason='stop', usage={'total_tokens':3})
        return await original(messages, **kwargs)

    kernel.llm.chat=faults
    result=run(kernel)
    trace=result['access']['adaptive']
    # The repair could not be budgeted; the already-validated Reader answer is
    # published with an explicit support-rejected marker instead of discarded.
    assert result['status']=='answer' and result['citations']
    assert result['validation']['support_rejected_published'] is True
    assert trace['policy']['calls']==7 and trace['policy']['max_calls']==8
    assert trace['policy']['reserved_calls']==0 and trace['retrieval_rounds']==1
    assert trace['answer_repair']['reason']=='repair_budget_unavailable'
    assert not trace['answer_repair']['attempted'] and counts['repair']==0
    assert len(model.calls)==7  # JSON repairs are charged, no hidden repair call.


@pytest.mark.parametrize('answer', ['Alice did not go.', 'Bob went.', 'Alice went in 2024.', 'Alice'])
def test_literal_repair_cannot_drop_negation_identity_date_or_part_of_a_unit(answer):
    with pytest.raises(ValueError, match='introduced or altered'):
        validate_deletion(ReaderAnswer(status='answer', answer=answer, evidence_ids=['id']), ['Alice went.'], ['id'])


def test_repair_preserves_necessary_citations_and_whole_unit_order():
    value=ReaderAnswer(status='answer', answer='Alice went. It was conditional.', evidence_ids=['id'])
    validate_deletion(value, ['Alice went.','It was conditional.'], ['id'])
    with pytest.raises(ValueError, match='necessary citations'):
        validate_deletion(value, ['Alice went.','It was conditional.'], ['id','identity'])
    value.answer='It was conditional. Alice went.'
    with pytest.raises(ValueError, match='introduced or altered'):
        validate_deletion(value, ['Alice went.','It was conditional.'], ['id'])


def test_global_identity_blocker_cannot_be_repaired_by_selectively_removing_words():
    check={'rule_blockers':['requested_subject_not_grounded_in_quotes'], 'verdict':'supported'}
    assert retained_units(check, 'Alice went.')==[]


def source_date(value, statement, *, scope='source_metadata'):
    evidence=[{'id':'e1','speaker':'Alice','text':'I accepted a research internship.', 'session_date_raw':'27 May 2024'}]
    answer={'answer':statement,'evidence_ids':['e1']}
    decision={'verdict':'supported','claims':[{'statement':statement,'relation':'supported','basis':'explicit',
        'scope':scope,'sources':[{'id':'e1','field':'session_date_raw','quote':value}], 'reason':'Synthetic.'}],
        'missing_queries':[], 'reason':'Synthetic.'}
    return AnswerRisk().validate(decision, 'What did Alice accept?', answer, evidence)


def test_explicit_source_clock_statement_does_not_require_a_fabricated_event():
    result=source_date('27 May 2024', 'The source date is 27 May 2024.')
    assert result['accepted'] and not result['support_dependencies']
    assert result['support_contract']['source_clock_distinct_from_event_time']


@pytest.mark.parametrize('statement', ['Alice accepted on 27 May 2024.',
    'The source date is 27 May 2024, so Alice accepted on that day.',
    'The source date is 28 May 2024.', '来源日期为27 May 2024，所以当天已完成。'])
def test_event_date_cannot_be_disguised_as_source_metadata(statement):
    result=source_date('27 May 2024', statement)
    assert not result['accepted'] and result['claim_rule_blockers']


def test_default_fact_still_rejects_bare_clock_and_partial_metadata_anchor():
    with pytest.raises(ValueError, match='Timestamp alone'):
        source_date('27 May 2024', '27 May 2024', scope='fact')
    with pytest.raises(ValueError, match='complete original date'):
        source_date('May 2024', 'The source date is May 2024.')


def test_entailed_half_answer_cannot_pass_repair_with_all_citation_ids_retained():
    answer={'answer':'A cat.', 'evidence_ids':['cat','dog']}
    evidence=[{'id':'cat','text':'Alice adopted a cat.'}, {'id':'dog','text':'Alice also adopted a dog.'}]
    query_plan={'requirements':[{'fact':'first pet'}, {'fact':'second pet'}]}
    value={'verdict':'supported', 'claims':[{'statement':'A cat.', 'relation':'supported', 'basis':'explicit',
        'sources':[{'id':'cat','quote':'Alice adopted a cat.'}], 'reason':'Synthetic.'}],
        'requirement_coverage':[{'fact':'first pet','status':'answered','answer_units':['A cat.']},
                                {'fact':'second pet','status':'missing','answer_units':[]}],
        'missing_queries':[], 'reason':'Entailed but incomplete answer.'}
    result=AnswerRisk().validate(value, 'Which two pets?', answer, evidence, query_plan=query_plan)
    assert not result['accepted'] and result['question_requirement_coverage_verified']
    value['requirement_coverage'].pop()
    with pytest.raises(ValueError, match='all frozen answer requirements'):
        AnswerRisk().validate(value, 'Which two pets?', answer, evidence, query_plan=query_plan)


@pytest.mark.parametrize('split', [False, True])
@pytest.mark.parametrize('event,date,accepted', [
    ('I accepted a research internship.', 'on 27 May 2024', False),
    ('I just accepted a research internship.', 'on 27 May 2024', False),
    ('I recently accepted a research internship.', 'on 27 May 2024', False),
    ('I accepted a research internship last month.', 'on 27 May 2024', False),
    ('I accepted a research internship last year.', 'on 27 May 2023', False),
    ('I accepted a research internship yesterday.', 'on 26 May 2024', True),
    ('I accepted a research internship last year.', 'in 2023', True),
    ('I accepted a research internship last year.', 'in 2024', False),
    ('I accepted a research internship last month.', 'in April 2024', True),
])
def test_event_clock_derivation_needs_quoted_time_and_preserves_precision(split,event,date,accepted):
    statement='Alice accepted a research internship'
    text_quote={'id':'e1','field':'text','quote':event}
    clock_quote={'id':'e1','field':'session_date_raw','quote':'27 May 2024'}
    claims=[{'statement':statement,'relation':'supported','basis':'explicit',
        'sources':[text_quote],'reason':'Synthetic.'},
        {'statement':date,'relation':'supported','basis':'derived','sources':[clock_quote],'reason':'Synthetic.'}]
    if not split:
        claims=[{'statement':statement+' '+date,'relation':'supported','basis':'derived',
            'sources':[text_quote,clock_quote],'reason':'Synthetic.'}]
    value={'verdict':'supported','claims':claims,'missing_queries':[],'reason':'Synthetic.'}
    answer={'answer':statement+' '+date,'evidence_ids':['e1']}
    evidence=[{'id':'e1','speaker':'Alice','text':event,'session_date_raw':'27 May 2024'}]
    check=AnswerRisk().validate(value,'When did Alice accept the internship?',answer,evidence)
    assert check['accepted']==accepted
    if not accepted:
        assert check['claim_rule_blockers'] and check['quote_integrity_verified']
        if split:
            assert retained_units(check,answer['answer'])==[statement]


def test_clock_only_event_date_must_be_derived_even_with_an_event_time_quote():
    evidence=[{'id':'e1','speaker':'Alice','text':'I moved yesterday.','session_date_raw':'27 May 2024'}]
    value={'verdict':'supported','claims':[
        {'statement':'Alice moved','relation':'supported','basis':'explicit',
         'sources':[{'id':'e1','quote':'I moved yesterday.'}],'reason':'Synthetic.'},
        {'statement':'on 26 May 2024','relation':'supported','basis':'explicit',
         'sources':[{'id':'e1','field':'session_date_raw','quote':'27 May 2024'}],'reason':'Synthetic.'}],
        'missing_queries':[],'reason':'Synthetic.'}
    check=AnswerRisk().validate(value,'When did Alice move?',
        {'answer':'Alice moved on 26 May 2024','evidence_ids':['e1']},evidence)
    assert not check['accepted'] and 'clock_only_fact_requires_derived_basis' in check['rule_blockers']
