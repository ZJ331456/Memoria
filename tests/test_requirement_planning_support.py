"""Isolated synthetic planning/contract regressions, never quality labels."""
import pytest

from memoria.memory.adaptive_retrieval import requirement_order, requirement_diagnostics, SUPPORT_SYSTEM
from memoria.memory.answer_risk import AnswerRisk
from memoria.memory.evidence.query_plan import plan, supplemental_targets, validate_plan
from memoria.memory.evidence_sufficiency import Quote, validate_quotes


COMPOUND = ('Why did Alex choose the approach, what did they change afterward, '
            'and which problem did that solve?')


def test_requested_change_and_causal_result_are_independent_frozen_requirements():
    result = plan(COMPOUND, {'subject_id':'subject:request', 'context':{'during':'migration'}})
    assert [s['id'] for s in result['requirement_specs']] == ['adoption','reason','change','resolution']
    assert result['subject_id']=='subject:request' and result['context']=={'during':'migration'}
    for spec in result['requirement_specs']:
        assert COMPOUND[slice(*spec['question_span'])] == spec['question_text']
        assert 'Alex' in spec['locator'] and len(spec['locator'])<=300
    assert result['requirement_specs'][2]['question_text']=='what did they change afterward'
    assert result['requirement_specs'][3]['question_text']=='which problem did that solve'
    assert not result['gold_used'] and result['unknowns_preserved']


@pytest.mark.parametrize('question', [
    'What approach might Alex use?', 'When did Alice move later that year?',
    'Why are flowers important to Melanie?', 'What does the problem mean to Alex?',
])
def test_bare_approach_or_narrative_time_does_not_invent_adoption_or_extra_event(question):
    result=plan(question,{})
    assert len(result['requirements'])==1
    assert 'adoption' not in [s['id'] for s in result['requirement_specs']]


def test_unknown_multi_part_questions_retain_each_literal_request_without_guessing_answers():
    question='Where did Alice travel and what did she bring back and who joined her?'
    result=plan(question,{})
    assert len(result['requirements'])==3
    assert [s['question_text'] for s in result['requirement_specs']]==[
        'Where did Alice travel','what did she bring back','who joined her']
    assert all(s['id'].startswith('detail_') for s in result['requirement_specs'])


def test_single_clause_can_request_both_adoption_and_its_outcome():
    specs=plan('Which approach did Alex choose and its outcome?',{})['requirement_specs']
    assert {'adoption','outcome'} <= {s['id'] for s in specs}


def test_requirement_limit_preserves_the_remaining_question_instead_of_deleting_it():
    question=' and '.join('what was item '+str(i) for i in range(10))+'?'
    result=plan(question,{})
    assert len(result['requirements'])==8
    assert result['requirement_specs'][-1]['id']=='remaining'
    assert result['requirement_specs'][-1]['question_text']==question


def test_overflow_from_structured_requirements_also_keeps_complete_remaining_question():
    question=('Which project did both Alice and Bob choose and why; how long did it take; '
              'what changed afterward; which problem did that solve?')
    result=plan(question,{})
    assert len(result['requirements'])==8
    assert result['requirement_specs'][-1]['id']=='remaining'
    assert result['requirement_specs'][-1]['question_text']==question


@pytest.mark.parametrize('question,structured_id', [
    ('How many cities did Alex visit, and who joined Alex?', 'event_count'),
    ('Which activities did Alex enjoy, and where did Alex stay?', 'collection'),
    ('How long did Alex work there, and who hired Alex?', 'interval_end'),
    ('What do both Alice and Bob enjoy, and where did Alice stay?', 'intersection'),
])
def test_structured_collection_count_and_intersection_do_not_consume_other_subquestions(question,structured_id):
    specs=plan(question,{})['requirement_specs']
    assert structured_id in {s['id'] for s in specs}
    assert any(s['id'].startswith('detail_') and s['question_text'].startswith(('who','where')) for s in specs)


def missing_decision(query_plan):
    return {'state':'missing_fact','requirements':[
        {**r,'status':'missing','sources':[]} for r in query_plan['requirements']],
        'missing_queries':['Unrelated unknown person fabricated result'], 'reason':'Synthetic missingness.'}


def test_supplement_targets_only_missing_requirements_and_retains_original_subject():
    query_plan=plan(COMPOUND,{})
    decision=missing_decision(query_plan)
    decision['requirements'][0]['status']='supported'
    targets=supplemental_targets(query_plan,decision)
    assert len(targets)==2
    assert [t['requirement'].split(':')[0] for t in targets]==['reason','change']
    assert all('Alex' in t['query'] and 'fabricated' not in t['query'] for t in targets)
    assert all(t['basis']=='question_locator' for t in targets)
    assert query_plan['requirements'][0]['fact'].startswith('adoption:')


def test_model_suggestions_augment_but_cannot_replace_literal_missing_clause():
    query_plan=plan(COMPOUND,{})
    decision=missing_decision(query_plan)
    decision['missing_queries']=['Alex caching bottleneck']
    targets=supplemental_targets(query_plan,decision)
    assert targets[0]['basis']=='question_locator_with_advice'
    assert query_plan['requirement_specs'][0]['question_text'] in targets[0]['query']
    # Presence of an answer-like suggestion is never proof that it is correct.
    assert validate_plan(decision,[],query_plan)['state']=='missing_fact'


def candidate(cid, content):
    return {'id':cid,'data':{'content':content},'kind':'library'}


def test_gate_inspection_reaches_complementary_low_ranked_change_within_bounded_pool():
    candidates=[candidate(str(i),'Alex chose the approach for deployment.') for i in range(20)]
    candidates.extend([candidate('change','Alex changed the data structure afterward.'),
                       candidate('solve','This solved the lock contention problem.')])
    ordered=requirement_order(candidates,plan(COMPOUND,{}))
    assert {'change','solve'} <= {c['id'] for c in ordered[:16]}
    assert {c['id'] for c in ordered}=={c['id'] for c in candidates}
    assert ordered[:2]==candidates[:2]
    prioritized=requirement_order(candidates,plan(COMPOUND,{}),priority_ids={'18','19'})
    assert [c['id'] for c in prioritized[:2]]==['18','19']


def test_diagnostics_never_confuse_assessed_missingness_with_corpus_absence():
    decision={'requirements':[
        {'fact':'r0','status':'supported','sources':[{'id':'quote'}]},
        {'fact':'r1','status':'missing','sources':[]}]}
    selection={'payload':{'evidence':[]},'complete':False,'fits':False}
    rows=requirement_diagnostics(decision,{'quote':['unit']},selection,['unit'],[],['unseen'])
    assert rows[0]['evidence_state']=='supported_group_over_budget'
    assert rows[0]['initially_recalled_but_not_selected']==['unit']
    assert rows[1]['evidence_state']=='not_supported_in_assessed_pool'
    assert all(r['unassessed_candidates_remain'] and not r['corpus_absence_established'] for r in rows)
    assert not any(r['presented_to_reader'] for r in rows)


def test_newly_retrieved_support_is_not_mislabeled_as_initial_selection_loss():
    decision={'requirements':[{'fact':'r0','status':'supported','sources':[{'id':'quote'}]}]}
    selected={'payload':{'evidence':[candidate('new','Original.')]},'complete':True,'fits':True}
    row=requirement_diagnostics(decision,{'quote':['new']},selected,['old'],[],[])[0]
    assert not row['initially_recalled_but_not_selected']
    assert row['supplementally_recalled']==['new'] and row['selected_for_reader']
    assert not row['presented_to_reader']  # It is still only a planned Reader packet.


def support(answer, quotes, *, basis='explicit'):
    return {'verdict':'supported','claims':[{'statement':answer,'relation':'supported','basis':basis,
        'sources':quotes,'reason':'Synthetic semantic advice.'}], 'missing_queries':[], 'reason':'Synthetic.'}


@pytest.mark.parametrize('answer,source',[
    ('In 2021.','She began working at the hospital in 2021.'),
    ('Alice prefers tea.','Alice likes tea.'),
    ('Alex will attend if the trip is funded.','If funding comes through, Alex plans to attend.'),
])
def test_answer_paraphrase_and_short_answer_have_separate_exact_quote_validation(answer,source):
    evidence=[{'id':'c1','text':source}]
    value=support(answer,[{'id':'c1','quote':source}])
    result=AnswerRisk().validate(value,'What did the source say?',{'answer':answer,'evidence_ids':['c1']},evidence)
    assert result['accepted'] and result['answer_unit_coverage_verified']
    assert result['support_contract']['paraphrases_allowed']
    assert not result['semantic_entailment_verified']
    value['claims'][0]['sources'][0]['quote']=answer
    with pytest.raises(ValueError,match='Quote not present'):
        AnswerRisk().validate(value,'What?',{'answer':answer,'evidence_ids':['c1']},evidence)


def test_relative_date_derivation_needs_same_source_clock_and_event_quote():
    evidence=[{'id':'c1','speaker':'Alice','text':'I moved last year.','session_date_raw':'8 May 2023'},
              {'id':'c2','speaker':'Alice','text':'Another event.','session_date_raw':'8 May 2023'}]
    answer={'answer':'In 2022.','evidence_ids':['c1','c2']}
    value=support(answer['answer'],[{'id':'c1','quote':'I moved last year.'},
        {'id':'c1','field':'session_date_raw','quote':'8 May 2023'}],basis='derived')
    assert AnswerRisk().validate(value,'When did Alice move?',answer,evidence)['accepted']
    value['claims'][0]['sources'][1]['id']='c2'
    with pytest.raises(ValueError,match='anchor must match'):
        AnswerRisk().validate(value,'When did Alice move?',answer,evidence)


def test_subject_name_fragment_cannot_ground_another_speakers_claim():
    evidence=[{'id':'c1','speaker':'Bob','text':'I am planning a workshop.'},
              {'id':'c2','speaker':'Ann','text':'I enjoy tea.'}]
    value=support('A workshop.',[{'id':'c1','quote':evidence[0]['text']}])
    result=AnswerRisk().validate(value,'What is Ann planning?',{'answer':'A workshop.','evidence_ids':['c1']},evidence)
    assert not result['accepted'] and 'requested_subject_not_grounded_in_quotes' in result['rule_blockers']


def test_third_party_explicit_named_subject_remains_valid_with_independent_advice():
    evidence=[{'id':'c1','speaker':'Bob','text':'Alice prefers tea.'},
              {'id':'c2','speaker':'Alice','text':'Hello.'}]
    value=support('Tea.',[{'id':'c1','quote':'Alice prefers tea.'}])
    assert AnswerRisk().validate(value,'What does Alice prefer?',{'answer':'Tea.','evidence_ids':['c1']},evidence)['accepted']


def test_noncontiguous_or_fabricated_causal_quotes_still_fail_even_for_supported_advice():
    evidence=[{'id':'c1','text':'Changed the cache.\nFewer delays.','source_texts':['Changed the cache.','Fewer delays.']}]
    for quote in ['Changed the cache.\nFewer delays.', 'Changed the cache, which caused fewer delays.']:
        with pytest.raises(ValueError,match='Quote not present'):
            validate_quotes([Quote(id='c1',quote=quote)],evidence)
    assert 'Chronological sequence does not itself establish a causal reason' in SUPPORT_SYSTEM
    assert 'plan, hope, invitation or intention remains future/conditional' in SUPPORT_SYSTEM
