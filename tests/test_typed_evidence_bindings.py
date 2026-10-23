"""Synthetic target/binding regressions; no gold, model calls or quality claims."""
from copy import deepcopy

import pytest

from memoria.memory.evidence.query_plan import plan, validate_plan, supplemental_targets
from memoria.memory.evidence_sufficiency import calendar_derivations
from memoria.memory.evidence.closure import select_closed
from memoria.memory.evidence.budget import EvidenceBudget


@pytest.mark.parametrize('query,target',[
    ('What kind of pottery does Morgan make?','attribute'),
    ('How does Morgan describe the feeling of swimming?','attribute'),
    ('How does Morgan describe the feeling when swimming?','attribute'),
    ('What did Morgan make for the school?','object'),
    ('What year did Morgan attend the festival?','time'),
    ('Would Morgan consider teaching without support?','inference'),
])
def test_target_types_are_frozen_without_candidates(query,target):
    value=plan(query,{})
    assert len(value['requirements'])==1
    assert value['requirement_specs'][0]['target_type']==target
    assert not value['gold_used'] and value['unknowns_preserved']


def decision(query,evidence,*,value='A vase',basis='explicit',missing=(),sources=None,scope=None):
    frozen=plan(query,{})
    if sources is None:sources=[{'id':evidence[0]['id'],'quote':evidence[0]['text']}]
    return frozen, {'state':'sufficient','requirements':[{**r,'status':'supported',
        'binding':{'value':value,'basis':basis,'missing_components':list(missing),'scope':scope},
        'sources':deepcopy(sources)} for r in frozen['requirements']],
        'missing_queries':[],'reason':'Synthetic independently bound source advice.'}


def test_relevant_but_incomplete_binding_is_a_real_gap_not_a_format_repair():
    evidence=[{'id':'a','text':'I like pottery and bright colors.'}]
    frozen,value=decision('What kind of pottery does Morgan make?',evidence,
        value='Pottery',missing=['target_attribute'])
    result=validate_plan(value,evidence,frozen)
    assert result['state']=='missing_fact' and result['model_state']=='sufficient'
    assert result['binding_decisions'][0]['blockers']==['binding_has_missing_components']
    assert supplemental_targets(frozen,result)
    assert not result['semantic_sufficiency_verified']


def test_supported_advice_must_identify_requested_value_and_binding():
    evidence=[{'id':'a','text':'I make blue pottery.'}]
    frozen,value=decision('What color pottery does Morgan make?',evidence,value='')
    assert validate_plan(value,evidence,frozen)['state']=='missing_fact'
    del value['requirements'][0]['binding']
    with pytest.raises(ValueError,match='target-specific binding'):validate_plan(value,evidence,frozen)


def test_missing_binding_value_can_be_null_without_repairing_a_valid_negative_judgment():
    evidence=[{'id':'a','text':'I make pottery.'}]
    frozen,value=decision('What kind of pottery does Morgan make?',evidence,value=None,
        missing=['target_attribute'])
    value['state']='missing_fact'
    value['requirements'][0].update(status='missing',sources=[])
    result=validate_plan(value,evidence,frozen)
    assert result['state']=='missing_fact' and result['model_state']=='missing_fact'
    assert result['requirements'][0]['binding']['value'] is None
    assert supplemental_targets(frozen,result)


def test_conflicted_binding_value_can_be_null_without_selecting_an_unresolved_value():
    evidence=[{'id':'a','text':'I make only blue pottery.'},
              {'id':'b','text':'I make only red pottery.'}]
    frozen,value=decision('What color pottery does Morgan make?',evidence,value=None,
        sources=[{'id':e['id'],'quote':e['text']} for e in evidence])
    value['state']='conflict'
    value['requirements'][0]['status']='conflicted'
    assert validate_plan(value,evidence,frozen)['state']=='conflict'


@pytest.mark.parametrize('query,basis',[
    ('What color pottery does Morgan make?','explicit'),
    ('What year did Morgan make pottery?','derived'),
])
def test_null_value_never_makes_a_supported_binding_sufficient(query,basis):
    evidence=[{'id':'a','text':'I made pottery last year.','session_date_raw':'17 August 2024'}]
    frozen,value=decision(query,evidence,value=None,basis=basis,
        sources=[{'id':'a','quote':evidence[0]['text']},
                 {'id':'a','field':'session_date_raw','quote':evidence[0]['session_date_raw']}])
    result=validate_plan(value,evidence,frozen)
    assert result['state']=='missing_fact'
    assert 'requested_value_not_identified' in result['binding_decisions'][0]['blockers']


def test_pronoun_only_object_quote_cannot_supply_its_missing_antecedent():
    evidence=[{'id':'a','text':'It was made for a local school.'},
              {'id':'b','text':'I made this ceramic vase.'}]
    frozen,value=decision('What did Morgan make for the school?',evidence)
    result=validate_plan(value,evidence,frozen)
    assert result['state']=='missing_fact'
    assert 'object_antecedent_not_bound' in result['binding_decisions'][0]['blockers']
    value['requirements'][0]['sources'].append({'id':'b','quote':evidence[1]['text']})
    result=validate_plan(value,evidence,frozen)
    assert result['state']=='sufficient'
    candidates=[{'id':e['id'],'data':{'content':e['text']}} for e in evidence]
    selection=select_closed(candidates,result,{'a':['a'],'b':['b']},EvidenceBudget(2000))
    assert selection['complete'] and selection['necessary_count']==2
    assert selection['groups'][0]['binding']['value']=='A vase'


def test_object_with_literal_apposition_is_not_rejected_as_unresolved():
    evidence=[{'id':'a','text':'It was made for a school as a ceramic vase.'}]
    frozen,value=decision('What did Morgan make for the school?',evidence)
    assert validate_plan(value,evidence,frozen)['state']=='sufficient'


def year_sources(evidence):
    return [{'id':'event','quote':evidence[0]['text']},
            {'id':'event','field':'session_date_raw','quote':evidence[0]['session_date_raw']}]


@pytest.mark.parametrize('basis',['derived','explicit'])
def test_same_source_last_year_derivation_is_auditable_without_an_absolute_event_year(basis):
    evidence=[{'id':'event','text':'We attended the festival last year.','session_date_raw':'17 August 2024'}]
    hints=calendar_derivations(evidence)
    assert len(hints)==1 and hints[0]['value']=='2023' and not hints[0]['event_identity_verified']
    frozen,value=decision('What year did Morgan attend the festival?',evidence,
        value='2023',basis=basis,sources=year_sources(evidence))
    result=validate_plan(value,evidence,frozen)
    assert result['state']=='sufficient' and not result['semantic_sufficiency_verified']
    value['requirements'][0]['binding']['value']='2022'
    assert 'derived_calendar_year_mismatch' in validate_plan(value,evidence,frozen)['binding_decisions'][0]['blockers']


def test_ambiguous_relative_and_absolute_year_quotes_are_not_forced_through_simple_arithmetic():
    evidence=[{'id':'event','text':'I enrolled in 2020 and attended the festival last year.',
               'session_date_raw':'17 August 2024'}]
    frozen,value=decision('What year did Morgan enroll?',evidence,value='2020',
        sources=year_sources(evidence))
    assert validate_plan(value,evidence,frozen)['state']=='sufficient'


def test_temporal_binding_does_not_borrow_a_clock_or_demand_more_precision_than_requested():
    evidence=[{'id':'event','text':'I attended the festival last year.','session_date_raw':'17 August 2024'},
              {'id':'other','text':'Another event.','session_date_raw':'17 August 2024'}]
    frozen,value=decision('What year did Morgan attend the festival?',evidence,value='2023',basis='derived')
    assert validate_plan(value,evidence,frozen)['state']=='missing_fact'
    value['requirements'][0]['sources'].append({'id':'other','field':'session_date_raw','quote':evidence[1]['session_date_raw']})
    with pytest.raises(ValueError,match='same utterance'):validate_plan(value,evidence,frozen)
    # A vague word may answer a vague request, but cannot invent a calendar year.
    recent=[{'id':'a','text':'I attended recently.'}]
    frozen,value=decision('When did Morgan attend?',recent,value='Recently')
    assert validate_plan(value,recent,frozen)['state']=='sufficient'
    frozen,value=decision('What year did Morgan attend?',recent,value='2023')
    assert validate_plan(value,recent,frozen)['state']=='missing_fact'


def test_modest_inference_policy_is_question_scoped_not_an_absolute_fact_permission():
    evidence=[{'id':'a','text':'Support motivated my teaching interest.'}]
    frozen,value=decision('Would Morgan consider teaching without support?',evidence,
        value='Possibly less likely',basis='inference')
    assert validate_plan(value,evidence,frozen)['state']=='sufficient'
    frozen,value=decision('What motivated Morgan to teach?',evidence,
        value='Support',basis='inference')
    assert validate_plan(value,evidence,frozen)['state']=='missing_fact'


@pytest.mark.parametrize('query,scope',[
    ('How many times did Morgan visit the lake?','specified_events'),
    ('How many times did Morgan mention visiting the lake?','recorded_mentions'),
])
def test_count_scope_cannot_silently_switch_between_mentions_and_events(query,scope):
    evidence=[{'id':'a','text':'I visited twice last year.'}]
    frozen,value=decision(query,evidence,value='Two',scope='unknown')
    assert {s['count_scope'] for s in frozen['requirement_specs']}=={scope}
    assert validate_plan(value,evidence,frozen)['state']=='missing_fact'
    for row in value['requirements']:row['binding']['scope']=scope
    assert validate_plan(value,evidence,frozen)['state']=='sufficient'


def test_similar_names_do_not_supply_unproven_identity_aliases():
    evidence=[{'id':'a','speaker':'Mara','text':'I visited the lake.'},
              {'id':'b','speaker':'Jules','text':'I visited the lake too.'}]
    frozen,value=decision('Which place did both Mary and Jules visit?',evidence,value='Lake',
        sources=[{'id':e['id'],'quote':e['text']} for e in evidence])
    result=validate_plan(value,evidence,frozen)
    assert result['state']=='missing_fact'
    row=next(r for r in result['binding_decisions'] if r['requirement'].startswith('subject_Mary:'))
    assert 'requested_subject_not_bound' in row['blockers']


def test_valid_negative_advice_is_not_promoted_by_complete_partial_bindings():
    evidence=[{'id':'a','text':'I make pottery.'}]
    frozen,value=decision('What kind of pottery does Morgan make?',evidence,value='Pottery')
    value['state']='unsupported'
    assert validate_plan(value,evidence,frozen)['state']=='unsupported'


def test_quotes_remain_exact_even_when_binding_value_paraphrases():
    evidence=[{'id':'a','text':'I make blue pottery.'}]
    frozen,value=decision('What color pottery does Morgan make?',evidence,value='Blue')
    assert validate_plan(value,evidence,frozen)['state']=='sufficient'
    value['requirements'][0]['sources'][0]['quote']='Pottery is blue.'
    with pytest.raises(ValueError,match='Quote not present'):validate_plan(value,evidence,frozen)
