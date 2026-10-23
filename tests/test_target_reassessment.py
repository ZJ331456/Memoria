"""Synthetic regressions for repeated recall and lost answer targets."""
import asyncio
import json

import pytest

from memoria.llm.types import ChatResult
from memoria.memory.access import QueryRequest
from memoria.memory.adaptive_retrieval import GATE
from memoria.memory.adaptive_fallback import AdaptiveFallback
from memoria.memory.answer_risk import AnswerRisk
from memoria.memory.evidence.query_plan import plan, supplemental_targets
from tests.test_adaptive_retrieval import core, QUERY

__all__ = ['core']


@pytest.mark.parametrize('advice', [[], ['Morgan pottery style']])
def test_one_missing_attribute_uses_distinct_second_probe_without_changing_requirements(advice):
    frozen=plan('What kind of pottery does Morgan make?',{'subject_id':'person:morgan'})
    decision={'requirements':[{**frozen['requirements'][0],'status':'missing'}], 'missing_queries':advice}
    targets=supplemental_targets(frozen,decision)
    assert len(targets)==2 and targets[0]['query']!=targets[1]['query']
    assert all(t['requirement']==frozen['requirements'][0]['fact'] and 'Morgan' in t['query'] for t in targets)
    assert frozen['subject_id']=='person:morgan' and len(frozen['requirements'])==1


def test_second_probe_preserves_a_different_model_locator_instead_of_repeating_first_advice():
    frozen=plan('What problem did Morgan encounter afterward?',{})
    decision={'requirements':[{**frozen['requirements'][0],'status':'missing'}],
        'missing_queries':['Morgan project problem','Morgan A17 later problem']}
    targets=supplemental_targets(frozen,decision)
    assert len(targets)==2 and 'A17' not in targets[0]['query'] and 'A17' in targets[1]['query']


@pytest.mark.parametrize('text,accepted', [
    ('Morgan moved this month.',False),
    ('Morgan moved this month after packing 2 boxes.',False),
    ('Morgan moved in September 2024.',True),
    ('Morgan moved this month, September 2024.',True),
])
def test_deletion_cannot_leave_when_answer_without_calendar_anchor(text,accepted):
    question='When did Morgan move?'
    frozen=plan(question,{})
    evidence=[{'id':'a','speaker':'Morgan','text':'I moved this month.', 'session_date_raw':'18 September 2024'}]
    value={'verdict':'supported', 'claims':[{'statement':text,'relation':'supported','basis':'explicit',
        'sources':[{'id':'a','quote':'I moved this month.'}], 'reason':'Synthetic advice.'}],
        'requirement_coverage':[{'fact':frozen['requirements'][0]['fact'],'status':'answered','answer_units':[text]}],
        'reason':'Synthetic advice.'}
    result=AnswerRisk().validate(value,question,{'answer':text,'evidence_ids':['a']},evidence,query_plan=frozen)
    assert result['accepted']==accepted
    assert ('relative_time_answer_missing_calendar_anchor' in result['rule_blockers'])==(not accepted)


def test_second_gate_sees_actual_local_gap_without_promoting_or_deleting_requirements(core):
    kernel,model,_=core
    original=model.chat
    gaps=[]

    async def chat(messages,**kwargs):
        if GATE in messages[0]['content']:
            packet=json.loads(messages[1]['content'])
            if model.gates:
                gaps.extend(packet['previous_gaps'])
                assert len(packet['query_plan']['requirements'])==4
            result=await original(messages,**kwargs)
            if model.gates==1:
                value=json.loads(result.content)
                value['requirements'][0]['binding']['value']=None
                result.content=json.dumps(value,ensure_ascii=False)
            return result
        return await original(messages,**kwargs)

    kernel.llm.chat=chat
    result=asyncio.run(kernel.answer(QueryRequest(query=QUERY,use_adaptive_retrieval=True,include_experiences=False)))
    assert result['status']=='answer',result['validation']
    assert model.gates==2 and gaps[0]['blockers']==['requested_value_not_identified']
    assert result['access']['adaptive']['retrieval_rounds']==1


def test_optional_format_retry_stops_before_call_when_remaining_complete_pipeline_cannot_fit(core,monkeypatch):
    kernel,model,_=core
    original=model.chat
    hold=AdaptiveFallback.hold_calls

    def limited_hold(self,name,reports):
        if self.calls>=2:
            return False
        return hold(self,name,reports)

    async def malformed_reader(messages,**kwargs):
        if GATE in messages[0]['content']:
            return await original(messages,**kwargs)
        model.calls.append(messages[0]['content'])
        return ChatResult(content='{broken',finish_reason='stop',usage={'total_tokens':3})

    monkeypatch.setattr(AdaptiveFallback,'hold_calls',limited_hold)
    kernel.llm.chat=malformed_reader
    result=asyncio.run(kernel.answer(QueryRequest(query=QUERY,use_adaptive_retrieval=True,include_experiences=False)))
    assert result['status']=='abstain' and not result['citations']
    assert result['validation']['reason']=='reader_repair_reservation_unavailable'
    assert len(model.calls)==2 and result['access']['adaptive']['policy']['reserved_calls']==0


@pytest.mark.parametrize('statement,valid',[
    ("Relative to the source's declared date of 8 May 2023, that is 2022.",True),
    ("Last year; relative to the source's original date of 8 May, 2023, this means 2022.",True),
    ("Relative to the source's declared date of 8 May 2024, that is 2022.",False),
    ("Relative to the source's declared date of 8 May 2023, that is 2021.",False),
    ("The event was on 8 May 2023, or in 2022.",False),
])
def test_source_anchor_inside_derivation_explanation_is_not_an_event_date(statement,valid):
    from memoria.memory.answer_dates import derivation_blockers
    blockers=derivation_blockers(statement,['I moved last year.'],['1:56 pm on 8 May, 2023'],
        clock_only=False,basis='derived')
    assert (not blockers)==valid


def test_complete_literal_support_retains_anchored_year_explanation():
    question='When did Morgan move?'
    frozen=plan(question,{})
    text="Morgan moved last year; relative to the source's declared date of 8 May 2023, that is 2022."
    evidence=[{'id':'a','speaker':'Morgan','text':'I moved last year.','session_date_raw':'1:56 pm on 8 May, 2023'}]
    value={'verdict':'supported','claims':[{'statement':text,'relation':'supported','basis':'derived',
        'sources':[{'id':'a','quote':'I moved last year.'},{'id':'a','field':'session_date_raw','quote':'1:56 pm on 8 May, 2023'}],
        'reason':'Synthetic arithmetic.'}],'requirement_coverage':[{'fact':frozen['requirements'][0]['fact'],
            'status':'answered','answer_units':[text]}],'reason':'Synthetic advice.'}
    result=AnswerRisk().validate(value,question,{'answer':text,'evidence_ids':['a']},evidence,query_plan=frozen)
    assert result['accepted'] and result['answer_unit_coverage_verified']
