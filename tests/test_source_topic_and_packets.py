"""Synthetic source-role, taxonomy and Reader-packet checks, not quality labels."""
import asyncio
import json
from copy import deepcopy

import pytest

from memoria.llm.types import ChatResult
from memoria.memory.source_formation import OBSERVE, VERIFY
from memoria.memory.adaptive_retrieval import Sources
from memoria.memory.evidence.budget import EvidenceBudget
from memoria.memory.evidence.closure import select_closed
from memoria.memory.evidence.source_windows import compact
from memoria.memory.evidence_sufficiency import Quote, validate_quotes
from memoria.memory.records import KnowledgeAnswer
from memoria.memory.read_diagnostics import failure, VerificationFailure
from tests.test_source_formation import env, model, add, form
from tests.test_source_units import transcript, create, observation, model as unit_model

__all__=['env']


def test_verified_topic_reuse_forms_one_bounded_group_from_three_distinct_sources(env):
    service,library,formation=env
    def alter(stage,data,output,_):
        if stage==OBSERVE:
            output['observations'][0]['topic']='配置检查'+str(len(data['source']))
            if offered:=data['existing_topics']:
                assert offered[0]['context_only']
                output['observations'][0]['topic_group_id']=offered[0]['group_id']
        if stage==VERIFY:
            output['verdicts'][0]['grouping_supported']=True
    calls=model(service,alter=alter)
    results=[form(formation,add(library,i,content='Memoria部署要检查配置。'+('附记。'*i))) for i in range(3)]
    assert len({r['observations'][0]['group_key'] for r in results})==1
    assert results[-1]['consolidation']['status']=='approved' and len(calls)==8
    assert results[-1]['consolidation_attempts'][0]['status']=='approved'
    assert len(formation.search('Memoria配置'))==1
    assert service.store.memories()==[]


def test_unchanged_first_group_does_not_starve_another_eligible_observation(env,monkeypatch):
    _,_,formation=env
    async def prepared(*_):return {'revision_id':'r','observations':[
        {'id':'first','status':'approved','group_key':'a'}, {'id':'next','status':'approved','group_key':'b'}]}
    async def consolidate(oid):return {'status':'no_new_evidence' if oid=='first' else 'approved'}
    monkeypatch.setattr(formation,'_form',prepared)
    monkeypatch.setattr(formation,'_complete',lambda _:True)
    monkeypatch.setattr(formation,'consolidate',consolidate)
    from memoria.memory.source_formation import FormRequest
    result=asyncio.run(formation.form('synthetic',FormRequest()))
    assert result['consolidation']['status']=='approved'
    assert [a['observation_id'] for a in result['consolidation_attempts']]==['first','next']


@pytest.mark.parametrize('fault',['unsupported','invented','subject','condition','history_changed'])
def test_taxonomy_rejection_cannot_merge_but_preserves_supported_current_fact(env,fault):
    service,library,formation=env
    model(service);prior=add(library,1);first=form(formation,prior)
    def alter(stage,data,output,_):
        if stage==OBSERVE:
            candidate=output['observations'][0];target=data['existing_topics'][0]
            candidate.update(topic_group_id=target['group_id'],topic=target['topic'])
            if fault=='invented':candidate['topic_group_id']='0'*64
            if fault=='subject':candidate['subject']='Other'
            if fault=='condition':candidate['conditions']='发布前'
        if stage==VERIFY:
            output['verdicts'][0]['grouping_supported']=fault!='unsupported'
            if fault=='history_changed':library.retract(prior['id'])
    model(service,alter=alter)
    second=form(formation,add(library,2,content='Other与Memoria部署前核对配置。'))
    assert second['observations'][0]['status']=='approved'
    assert second['observations'][0]['group_key']!=first['observations'][0]['group_key']
    rows=service.store.db.execute('SELECT verdict_json FROM library_cards ORDER BY rowid').fetchall()
    assert json.loads(rows[-1][0])['topic_link']['status']!='verified'
    assert formation.search('Memoria')==[]


def test_named_event_subject_is_separate_from_utterance_speaker(env):
    service,library,formation=env
    text,units=transcript(('Alice','Bob adopted a cat yesterday.'))
    item=create(library,text,units)
    unit_model(service,observation(subject='Bob',quote='Bob adopted a cat yesterday.'))
    result=form(formation,item)
    assert result['observations'][0]['status']=='approved'
    verdict=json.loads(service.store.db.execute('SELECT verdict_json FROM library_cards').fetchone()[0])
    assert verdict['source_attribution']['utterance_speaker']=='Alice'
    assert verdict['source_attribution']['event_subject']=='Bob'
    assert 'subject为该片段明确的speaker' not in OBSERVE
    assert '"topic_group_id":' in OBSERVE.split('OBSERVE +=')[0]


def test_topic_offers_cannot_read_other_working_session_or_retracted_sources(env):
    service,library,formation=env;model(service)
    first_session=service.store.create_session('Synthetic A')['id']
    second_session=service.store.create_session('Synthetic B')['id']
    prior=add(library,1,scope='working',session_id=first_session);form(formation,prior)
    own=add(library,2,scope='working',session_id=first_session)
    other=add(library,3,scope='working',session_id=second_session)
    assert formation._topics(library._source(own['id']),'Memoria配置')
    assert formation._topics(library._source(other['id']),'Memoria配置')==[]
    library.retract(prior['id'])
    assert formation._topics(library._source(own['id']),'Memoria配置')==[]


@pytest.mark.parametrize('old_policy,expected',[
    ('source-formation-v9-source-local-attribution',True),('source-formation-v8',False)])
def test_additive_topic_policy_preserves_only_previously_safe_source_receipts(env,monkeypatch,old_policy,expected):
    import memoria.memory.source_formation as module
    service,library,formation=env;model(service)
    monkeypatch.setattr(module,'POLICY',old_policy)
    item=add(library,1);form(formation,item)
    assert formation._complete(item['head_id']) is expected


def test_necessary_group_cost_uses_actual_reader_packet_and_compacts_literal_windows(env):
    service,library,_=env
    body='I adopted a cat yesterday. '+''.join(f'The decorative sample numbered {i} is blue. ' for i in range(120))
    body+='But I did not adopt a dog.'
    text,units=transcript(('Alice',body));create(library,text,units)
    adapter=Sources(service.kernel,KnowledgeAnswer(query='What did Alice adopt?',budget_chars=3000))
    access=asyncio.run(adapter.retrieve());candidate=access['candidates'][0];cid=candidate['id']
    assert candidate['data']['content']==body and candidate['data']['start']==units[0]['body_start']
    decision={'state':'sufficient','requirements':[{'fact':'adopted pet','status':'supported','sources':[
        {'id':cid,'field':'text','quote':'I adopted a cat yesterday.'}]}]}
    value={**access,'closure_advice':{'decision':decision}}
    render=lambda chosen:adapter.render(value,chosen)
    before=select_closed([candidate],decision,{cid:[cid]},EvidenceBudget(3000),render=render)
    assert not before['fits'] and before['required_cost']>3000
    pool=compact([candidate],decision)
    after=select_closed(pool,decision,{cid:[cid]},EvidenceBudget(3000),render=render)
    assert after['fits'] and after['required_cost']==len(json.dumps(render(pool),ensure_ascii=False,sort_keys=True,separators=(',',':')))
    final=adapter.final_access(value,after)
    guard,_,evidence,_=adapter.present(final,pool)
    assert guard.freshness() is None
    assert 'did not adopt a dog' in evidence[0]['text']
    assert candidate['data']['content']==body  # original candidates unchanged
    assert all(text[w['start']:w['end']]==w['text'] for w in pool[0]['data']['excerpts'])
    validate_quotes([Quote(id=cid,quote='I adopted a cat yesterday.')],evidence)
    windows=pool[0]['data']['excerpts']
    assert len(windows)>1
    # Cross-gap concatenation and mutated offsets never become source evidence.
    joined=windows[0]['text'][-6:]+'\n'+windows[1]['text'][:6]
    with pytest.raises(ValueError):validate_quotes([Quote(id=cid,quote=joined)],evidence)
    guard.packet['evidence'][0]['excerpts'][0]['text']='fabricated'
    assert guard.freshness()=='source_changed'


def test_closed_selector_keeps_all_required_units_and_does_not_raise_limit_to_fit():
    candidates=[{'id':str(i),'data':{'content':'A fact.'}} for i in range(3)]
    decision={'state':'sufficient','requirements':[{'fact':'all','status':'supported',
        'sources':[{'id':str(i),'quote':'A fact.'} for i in range(3)]}]}
    result=select_closed(candidates,decision,{str(i):[str(i)] for i in range(3)},EvidenceBudget(2000),limit=2)
    assert not result['fits'] and result['necessary_count']==3 and result['payload']['evidence']==[]


@pytest.mark.parametrize('counterevidence',[False,True])
def test_production_kernel_retries_selection_once_with_exact_source_windows(env,counterevidence):
    from memoria.memory.adaptive_retrieval import GATE
    from memoria.memory.answer_risk import SYSTEM as SUPPORT
    from memoria.prompting.assembler import _parse_frame
    service,library,_=env
    body='I adopted a cat. '+''.join(f'The decorative sample numbered {i} is blue. ' for i in range(120))
    if counterevidence:body+='That was a fictional example.'
    text,units=transcript(('Alice',body));create(library,text,units)
    calls=[]
    async def chat(messages,**_):
        system=messages[0]['content'];calls.append(system)
        if GATE in system:
            packet=json.loads(messages[1]['content']);cid=packet['evidence'][0]['id']
            assert 'speaker' not in packet['evidence'][0]['text']
            value={'state':'sufficient','requirements':[{**r,'status':'supported',
                'binding':{'value':'A cat','basis':'explicit','missing_components':[]},'sources':[
                {'id':cid,'quote':'I adopted a cat.'}]} for r in packet['query_plan']['requirements']],
                'missing_queries':[],'reason':'Synthetic original event.'}
        elif SUPPORT in system:
            packet=json.loads(messages[1]['content']);cid=packet['evidence'][0]['id']
            assert packet['evidence'][0]['verification_context_only']==body
            assert 'verification_context_only' not in packet['evidence'][0]['quote_fields']
            if counterevidence:
                assert 'fictional' not in packet['evidence'][0]['text']
                assert 'fictional' in packet['evidence'][0]['verification_context_only']
            value={'verdict':'insufficient' if counterevidence else 'supported','claims':[{'statement':packet['proposed_answer'],'relation':'insufficient' if counterevidence else 'supported',
                'basis':'explicit','sources':[{'id':cid,'quote':'I adopted a cat.'}],'reason':'Synthetic.'}],
                'missing_queries':[],'reason':'Synthetic.'}
            if 'query_plan' in packet:
                value['requirement_coverage']=[{'fact':r['fact'],'status':'missing' if counterevidence else 'answered',
                    'answer_units':[] if counterevidence else [packet['proposed_answer']]}
                    for r in packet['query_plan']['requirements']]
        else:
            _,sections=_parse_frame(messages[1]['content'])
            packet=json.loads(next(s.content for s in sections if s.name=='Source Evidence').rsplit('\n',1)[-1])
            assert packet['evidence'][0]['presentation']=='source_windows'
            value={'status':'answer','answer':'A cat.','evidence_ids':packet['required_evidence_ids']}
        return ChatResult(content=json.dumps(value),raw_message={},usage={'total_tokens':20},finish_reason='stop')
    service.llm.chat=chat
    result=asyncio.run(service.kernel.answer(KnowledgeAnswer(query='What did Alice adopt?',
        budget_chars=3000,use_adaptive_retrieval=True)))
    assert result['status']==('abstain' if counterevidence else 'answer'),result.get('validation')
    if counterevidence:assert result['validation']['reason']=='answer_support_not_established'
    trace=result['access']['adaptive']
    assert trace['rounds'][0]['selection']['compaction']['fits']
    assert len(calls)==3 and trace['retrieval_rounds']==0


def test_structured_contract_failure_has_no_source_or_answer_text():
    secret_text='private source and missing token'
    exc=VerificationFailure('support','answer_units_uncovered',ValueError(secret_text))
    report=failure('reader','generic',exc)
    assert report['stage']=='support' and report['category']=='contract' and report['technical_failure']
    assert report['detail_sha256'] and secret_text not in json.dumps(report)+str(exc)
    assert not failure('coverage','required_evidence_not_established')['technical_failure']
    assert failure('selection','necessary_group_over_budget')['technical_failure']
    import httpx
    from memoria.llm.types import ProviderError
    transport=ProviderError('Private provider error body')
    transport.__cause__=httpx.ReadTimeout('Private transport detail')
    report=failure('coverage','generic',transport)
    assert report['code']=='provider_read_timeout' and report['category']=='timeout'
    assert report['cause_types']==['ProviderError','ReadTimeout']
    assert 'Private' not in json.dumps(report)


def test_supported_semantic_paraphrase_is_not_required_to_be_verbatim_source():
    from memoria.memory.answer_risk import AnswerRisk
    answer={'answer':'Alice favors tea.','evidence_ids':['c1']}
    evidence=[{'id':'c1','text':'Alice likes tea.'}]
    value={'verdict':'supported','claims':[{'statement':answer['answer'],'relation':'supported','basis':'explicit',
        'sources':[{'id':'c1','quote':'Alice likes tea.'}],'reason':'Synthetic independent synonym judgment.'}],
        'missing_queries':[],'reason':'Synthetic.'}
    assert AnswerRisk().validate(deepcopy(value),'What does Alice prefer?',answer,evidence)['accepted']
    value['claims'][0]['sources'][0]['quote']='Alice favors tea.'
    with pytest.raises(ValueError):AnswerRisk().validate(value,'What does Alice prefer?',answer,evidence)
