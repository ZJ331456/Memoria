"""Synthetic contract/security checks; no benchmark scores or paid calls."""
import asyncio
import json

import pytest

from memoria.llm.types import ChatResult
from memoria.memory.library import SourceInput, RevisionInput, LibraryError
from memoria.memory.records import KnowledgeAnswer
from memoria.memory.source_formation import FormRequest, OBSERVE, VERIFY
from memoria.memory.source_units import SourceUnit, render_turns, validate_units, load
from tests.test_source_formation import env  # isolated kernel fixture


def transcript(*turns):
    return render_turns([{'id':f't{i}','speaker':speaker,'text':text,
        'session_date_raw':'1:56 pm on 8 May, 2023'} for i,(speaker,text) in enumerate(turns)])


def create(library,content,units):
    return library.create(SourceInput(kind='note',authorship='external',title='Synthetic transcript',
                                      content=content,source_units=units))


def observation(subject='Alice',unit='t0',quote='I adopted a cat yesterday.'):
    return {'category':'experience','topic':'pet adoption','subject':subject,'conditions':'',
        'polarity':'positive','epistemic':'asserted','content':subject+' reported adopting a cat yesterday.',
        'quote':quote,'source_unit_id':unit,'applicability':'Only this attributed source report.'}


def model(service,proposal,*,supported=True):
    async def chat(messages,**kwargs):
        prompt=messages[0]['content'];payload=json.loads(messages[1]['content'])
        if prompt==OBSERVE:
            assert payload['source_units']
            value={'observations':[proposal]}
        elif prompt==VERIFY:
            value={'verdicts':[{'id':c['id'],'assessment':'Supported' if supported else 'Insufficient',
                'attribution_preserved':supported,'conditions_preserved':True,'task_bound':True,
                'sensitive':False,'time_supported':False} for c in payload['candidates']]}
        else:raise AssertionError('No unbounded consolidation in fixture')
        return ChatResult(content=json.dumps(value),raw_message={},usage={'total_tokens':20})
    service.llm.chat=chat


def test_repeated_speaker_is_bound_by_the_exact_source_unit(env):
    service,library,formation=env
    text,units=transcript(('Alice','I adopted a cat yesterday.'),('Bob','Good to hear.'),('Alice','I enjoy hiking.'))
    source=create(library,text,units);model(service,observation())
    result=asyncio.run(formation.form(source['id'],FormRequest()))
    assert result['observations'][0]['status']=='approved'
    card=service.store.db.execute('SELECT * FROM library_cards').fetchone()
    assert json.loads(card['verdict_json'])['subject_binding_basis']=='literal_source_unit_header'
    assert service.store.memories()==[]
    assert service.store.db.execute('SELECT count(*) FROM claim_roots').fetchone()[0]==0
    semantics=json.loads(service.store.db.execute('SELECT data_json FROM memory_observation_semantics').fetchone()[0])
    assert semantics['source_role']=='external'
    assert semantics['event_time'] is None  # unknown timezone/clock is NOT upload time


def test_identical_utterance_is_disambiguated_only_by_presented_unit_id(env):
    service,library,formation=env
    text,units=transcript(('Alice','I adopted a cat yesterday.'),('Bob','I adopted a cat yesterday.'))
    source=create(library,text,units);model(service,observation(subject='Bob',unit='t1'))
    result=asyncio.run(formation.form(source['id'],FormRequest()))
    assert result['observations'][0]['status']=='approved'
    card=service.store.db.execute('SELECT start FROM library_cards').fetchone()
    assert card[0]==units[1]['body_start']


@pytest.mark.parametrize('subject,unit,supported',[('Bob','t0',True),('self','t0',True),('Alice','t0',False)])
def test_attribution_does_not_bypass_subject_or_independent_review(env,subject,unit,supported):
    service,library,formation=env
    text,units=transcript(('Alice','I adopted a cat yesterday.'))
    source=create(library,text,units);model(service,observation(subject,unit),supported=supported)
    result=asyncio.run(formation.form(source['id'],FormRequest()))
    assert result['observations'][0]['status']=='needs_assistance'


def test_unit_id_cannot_bind_an_unpresented_or_other_turn_quote(env):
    service,library,formation=env
    text,units=transcript(('Alice','I adopted a cat yesterday.'),('Bob','I enjoy hiking.'))
    source=create(library,text,units);model(service,observation(unit='t1'))
    with pytest.raises(LibraryError,match='没有有效观察'):
        asyncio.run(formation.form(source['id'],FormRequest()))


def test_bad_header_and_unindexed_text_are_rejected_before_ingest(env):
    _,library,_=env
    text,units=transcript(('Alice','I adopted a cat yesterday.'))
    with pytest.raises(ValueError,match='literal header'):create(library,text.replace('Alice','Other'),units)
    with pytest.raises(ValueError,match='trailing'):create(library,text+' hidden passage',units)
    assert library.list()==[]


def test_current_revision_retraction_and_scope_control_unit_visibility(env):
    service,library,_=env
    text,units=transcript(('Alice','I adopted a cat yesterday.'))
    source=create(library,text,units)
    result=asyncio.run(service.kernel.search(KnowledgeAnswer(query='Alice adopted cat')))
    record=result['evidence'][0]
    assert record['provenance']['source_unit']['speaker']=='Alice'
    assert record['provenance']['source_unit']['timezone']=='unknown'
    assert result['dependencies']
    revision=library.revise(source['id'],RevisionInput(expected_revision_id=source['head_id'],
        title='New source',content='No transcript attribution.'))
    assert not library.dependency_valid(result['dependencies'][0])
    assert load(service.store,library._revision(revision['head_id']))==[]
    library.retract(source['id'])
    assert asyncio.run(service.kernel.search(KnowledgeAnswer(query='Alice adopted cat')))['evidence']==[]


def test_date_anchors_are_top_level_source_local_fields_and_not_upload_dates(env):
    service,library,_=env
    from memoria.memory.adaptive_retrieval import Sources
    from memoria.memory.evidence.query_plan import plan, validate_plan
    text,units=transcript(('Alice','I adopted a cat yesterday.'))
    create(library,text,units)
    adapter=Sources(service.kernel,KnowledgeAnswer(query='When did Alice adopt a cat?'))
    access=asyncio.run(adapter.retrieve())
    _,_,evidence,_=adapter.present(access,access['candidates'])
    e=evidence[0]
    assert e['speaker']=='Alice' and e['session_date_raw']=='1:56 pm on 8 May, 2023'
    query_plan=plan(adapter.request.query,{})
    fact=query_plan['requirements'][0]
    decision={'state':'sufficient','requirements':[{**fact,'status':'supported',
        'binding':{'value':'7 May 2023','basis':'derived','missing_components':[]},'sources':[
        {'id':e['id'],'field':'text','quote':'I adopted a cat yesterday.'},
        {'id':e['id'],'field':'session_date_raw','quote':e['session_date_raw']}]}],
        'missing_queries':[],'reason':'Explicit utterance plus its source-local calendar.'}
    assert validate_plan(decision,evidence,query_plan)['quote_integrity_verified']
    bad=json.loads(json.dumps(decision));bad['requirements'][0]['sources'][1]['quote']='7 May 2023'
    with pytest.raises(ValueError):validate_plan(bad,evidence,query_plan)
    assert all('source_time' not in u for u in evidence)  # no invented UTC instant


def test_another_speaker_header_cannot_rebind_the_current_first_person_quote(env):
    service,library,formation=env
    text,units=transcript(('Alice','I adopted a cat yesterday.'),('Bob','I enjoy hiking.'))
    source=create(library,text,units)
    proposal=observation(subject='Bob')
    proposal['subject_quote']=text[units[1]['start']:units[1]['body_start']]
    model(service,proposal)
    result=asyncio.run(formation.form(source['id'],FormRequest()))
    assert result['observations'][0]['status']=='needs_assistance'


def test_raw_source_packet_is_stable_across_query_selected_cards(env):
    from copy import deepcopy
    from memoria.memory.adaptive_retrieval import Sources,source_packet_unit,merge_candidates
    service,library,_=env
    text,units=transcript(('Alice','I adopted a cat yesterday.'))
    create(library,text,units)
    adapter=Sources(service.kernel,KnowledgeAnswer(query='Alice adopted cat'))
    result=asyncio.run(adapter.retrieve())
    c=result['candidates'][0];e=deepcopy(c['data']);d=deepcopy(c['dependency'])
    e['provenance']['knowledge_cards']=[{'id':'query-selected-summary','content':'A different search hint'}]
    e['provenance']['observations']=[{'large_duplicate_summary':'x'*9000}]
    d['card_ids']=['query-selected-summary']
    d['observation_refs']=[{'revision_id':'lease-preserved'}]
    stable,dep=source_packet_unit(e,d)
    assert stable==c['data'] and dep['observation_refs']==d['observation_refs']
    assert dep['sha']==d['sha'] and stable['content']=='I adopted a cat yesterday.'
    assert stable['start']==units[0]['body_start'] and stable['end']==units[0]['end']
    assert 'speaker' not in stable['content'] and stable['provenance']['source_unit']['speaker']=='Alice'
    dep.pop('observation_refs')
    assert merge_candidates([c],[{**c,'data':stable,'dependency':dep}])==[c]
    assert e['provenance']['knowledge_cards']  # no mutation of the captured trace


def test_reader_receives_every_required_citation_and_full_packet_is_budgeted(env):
    from memoria.memory.adaptive_retrieval import Sources,SourceGuard
    service,library,_=env
    text,units=transcript(('Alice','I adopted a cat yesterday.'))
    create(library,text,units)
    adapter=Sources(service.kernel,KnowledgeAnswer(query='When did Alice adopt a cat?'))
    result=asyncio.run(adapter.retrieve());c=result['candidates'][0]
    result['closure_advice']={'decision':{'requirements':[{'sources':[{'id':c['id']}]}]}}
    guard,messages,_,_=adapter.present(result,[c])
    assert guard.packet['required_evidence_ids']==[c['id']]
    assert guard.visible(messages)
    assert adapter.budget().cost(guard.packet)<=8000


def test_query_facets_do_not_rewrite_every_reason_as_a_project_choice():
    from memoria.memory.evidence.query_plan import plan
    query=plan('Why are flowers important to Melanie?',{})
    assert '方案' not in query['requirements'][0]['fact']
    assert not plan('What happened to another project?',{})['negation']
    assert plan('Which project did she not choose?',{})['negation']


def test_missing_date_field_feedback_names_available_fields():
    from memoria.memory.evidence_sufficiency import Quote,validate_quotes
    with pytest.raises(ValueError,match='field session_date_raw is absent.*available fields: text'):
        validate_quotes([Quote(id='c1',field='session_date_raw',quote='8 May, 2023')],
                        [{'id':'c1','text':'Original source text.'}])


def test_uncovered_conjunction_is_reported_but_still_rejected():
    from memoria.memory.answer_risk import AnswerRisk
    value={'verdict':'supported','claims':[{'statement':s,'relation':'supported','basis':'explicit',
        'sources':[{'id':'c1','field':'text','quote':'Alice and Bob'}],'reason':'Synthetic.'} for s in ('Alice','Bob')],
        'missing_queries':[],'reason':'Synthetic.'}
    with pytest.raises(ValueError,match='not completely covered.*and'):
        AnswerRisk().validate(value,'Who?',{'answer':'Alice and Bob','evidence_ids':['c1']},
                              [{'id':'c1','text':'Alice and Bob'}])


def test_same_utterance_explicit_event_and_separate_date_keep_audited_dependencies():
    from memoria.memory.answer_risk import AnswerRisk
    value={'verdict':'supported','claims':[
        {'statement':'Alice painted it last year','relation':'supported','basis':'explicit',
         'sources':[{'id':'c1','field':'text','quote':'I painted it last year.'}],'reason':'Source self-report.'},
        {'statement':'The source date is 8 May 2023','relation':'supported','basis':'explicit','scope':'source_metadata',
         'sources':[{'id':'c1','field':'session_date_raw','quote':'8 May 2023'}],'reason':'Same utterance date.'},
        {'statement':'so in 2022','relation':'supported','basis':'derived',
         'sources':[{'id':'c1','field':'session_date_raw','quote':'8 May 2023'}],'reason':'Last year relative to 2023.'}],
        'missing_queries':[],'reason':'Synthetic.'}
    answer={'answer':'Alice painted it last year. The source date is 8 May 2023, so in 2022','evidence_ids':['c1']}
    evidence=[{'id':'c1','speaker':'Alice','text':'I painted it last year.','session_date_raw':'8 May 2023'}]
    result=AnswerRisk().validate(value,'When did Alice paint it?',answer,evidence)
    assert result['accepted'] and len(result['support_dependencies'])==1
    assert result['support_dependencies'][0]['event_claim_indices']==[0]
    value['claims'][0]['basis']='inference'
    with pytest.raises(ValueError,match='Timestamp alone'):AnswerRisk().validate(value,'When?',answer,evidence)


def test_date_dependency_cannot_borrow_another_source_or_an_unsupported_premise():
    from memoria.memory.answer_risk import AnswerRisk
    value={'verdict':'supported','claims':[
        {'statement':'Alice painted it','relation':'supported','basis':'explicit',
         'sources':[{'id':'c1','field':'text','quote':'I painted it.'}],'reason':'Synthetic.'},
        {'statement':'in 2023','relation':'supported','basis':'derived',
         'sources':[{'id':'c2','field':'session_date_raw','quote':'8 May 2023'}],'reason':'Synthetic.'}],
        'missing_queries':[],'reason':'Synthetic.'}
    answer={'answer':'Alice painted it in 2023','evidence_ids':['c1','c2']}
    evidence=[{'id':'c1','text':'I painted it.'},{'id':'c2','text':'Another event.','session_date_raw':'8 May 2023'}]
    with pytest.raises(ValueError,match='Timestamp alone'):AnswerRisk().validate(value,'When?',answer,evidence)
    value['claims'][1]['sources'][0]['id']='c1';evidence[0]['session_date_raw']='8 May 2023'
    value['claims'][0]['relation']='insufficient'
    with pytest.raises(ValueError,match='Timestamp alone'):AnswerRisk().validate(value,'When?',answer,evidence)


def test_shared_reader_support_reserves_worst_case_contract_feedback():
    from pathlib import Path
    from memoria.config import Settings
    from memoria.prompting.reader_budget import ReaderBudget
    from memoria.memory.verification import support_envelope,REPAIR_INSTRUCTION,REPAIR_FEEDBACK_CHARS
    from memoria.llm.deepseek_cache import shared_reader_messages
    settings=Settings.load(Path(__file__).resolve().parents[1]/'config/system.example.toml')
    meter=ReaderBudget(settings);packet={'question':'Synthetic','evidence':[]}
    shared=('Reader',{'question':'Synthetic','evidence':[]})
    bound=asyncio.run(support_envelope(meter,'Verify',packet,shared_reader=shared))
    messages=shared_reader_messages(settings.main,*shared,'Verify'+REPAIR_INSTRUCTION+'\x00'*REPAIR_FEEDBACK_CHARS,
                                    {**packet,'proposed_answer':'\x00'*4000})
    measured=asyncio.run(meter.measure(settings.main,messages,None,2048))
    assert measured['input_bytes']<=bound['input_bytes']
