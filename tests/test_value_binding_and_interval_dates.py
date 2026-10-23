"""Synthetic regressions found by pipeline audit, no labels used as retrieval input."""
import pytest

from memoria.memory.answer_dates import precision, derivation_blockers
from memoria.memory.evidence.query_plan import validate_plan
from tests.test_typed_evidence_bindings import decision


@pytest.mark.parametrize('text,unit', [('the week before 16 March 2024','week'),
    ('the month after 16 March 2024','month'), ('the year before 16 March 2024','year'),
    ('3月16日之前的一周','week')])
def test_interval_anchor_is_not_the_event_day(text,unit):
    assert precision(text)==unit


def test_week_answer_with_reference_day_keeps_its_source_precision():
    assert derivation_blockers('the week before 16 March 2024', ['I started last week.'],
        ['16 March 2024'],clock_only=False,basis='derived')==[]
    assert derivation_blockers('on 15 March 2024',['I started last week.'],
        ['16 March 2024'],clock_only=False,basis='derived')


def test_explicit_object_cannot_borrow_a_value_from_an_uncited_hint():
    evidence=[{'id':'event','text':'It was made for a school. I made it to symbolize growth.'},
              {'id':'object','text':'I made a cobalt glass lantern.'}]
    frozen,value=decision('What did Morgan make for the school?',evidence,value='cobalt glass lantern')
    result=validate_plan(value,evidence,frozen)
    assert result['state']=='missing_fact'
    assert 'requested_value_not_bound_to_cited_text' in result['binding_decisions'][0]['blockers']
    value['requirements'][0]['sources'].append({'id':'object','quote':evidence[1]['text']})
    assert validate_plan(value,evidence,frozen)['state']=='sufficient'


def test_utterance_clock_can_date_a_proposal_but_not_future_performance():
    text="Let's work together on the exhibition."
    clock='21 July 2024'
    evidence=[{'id':'event','text':text,'session_date_raw':clock}]
    sources=[{'id':'event','quote':text},{'id':'event','field':'session_date_raw','quote':clock}]
    frozen,value=decision('When did Morgan propose working together?',evidence,value=clock,sources=sources)
    assert validate_plan(value,evidence,frozen)['state']=='sufficient'
    assert derivation_blockers(clock,[text],[clock],clock_only=False,basis='explicit',
        question='When did Morgan propose working together?')==[]
    assert derivation_blockers(clock,[text],[clock],clock_only=False,basis='explicit',
        question='When did Morgan complete the exhibition?')


def test_as_of_derived_answer_uses_its_clock_precision():
    """An undated underway event + its same-source clock bounds a derived answer."""
    event = "I'm expanding my studio's social media presence and offering workshops."
    clock = '1:26 pm on 3 April, 2023'
    # Derived: the recording clock supplies the answer precision (month answer, day clock).
    assert derivation_blockers('April 2023', [event], [clock],
        clock_only=False, basis='derived',
        question="When did Jon start expanding his studio's social media presence?") == []
    # Explicit basis keeps the original boundary: an undated quote cannot time an event.
    assert derivation_blockers('April 2023', [event], [clock],
        clock_only=False, basis='explicit',
        question="When did Jon start expanding his studio's social media presence?") == [
        'event_time_expression_or_precision_not_bound']
    # A clock-only derived day answer stays forbidden at month clock precision.
    assert derivation_blockers('3 April 2023', [event], ['April 2023'],
        clock_only=True, basis='derived',
        question="When did Jon start expanding his studio's social media presence?") == [
        'event_time_expression_or_precision_not_bound']
