"""Temporary engineering counterexamples; never production history or quality scores."""
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from memoria.claims import ClaimMemory, ClaimDraft, ClaimRevision, ClaimDecision, ClaimError, ExtractionRequest, sha, canonical
from memoria.llm import ChatResult
from memoria.store import Store
from memoria.memory.access import MemoryAccessOrchestrator, QueryRequest
from memoria.memory.temporal_query import TemporalQueryParser
from memoria.memory.temporal_resolver import TemporalResolver
from memoria.memory.evidence_verifier import EvidenceVerifier
from memoria.memory.evidence import EvidenceBudget, requirements_for
from memoria.memory.evidence_selector import select_evidence
from memoria.memory.engine import MemoryEngine


@pytest.fixture
def core(tmp_path):
    store = Store(tmp_path/'isolated.db', vector_backend='json')
    claims = ClaimMemory(store,'engineering-owner')
    sid = store.create_session('Engineering only')['id']
    yield store, claims, sid
    store.close()


def llm_output(data, **kw):
    async def chat(*args, **kwargs):
        return ChatResult(content=json.dumps(data, ensure_ascii=False), raw_message={}, **kw)
    return SimpleNamespace(chat=chat)


def extract(core, text, candidates, **kw):
    message = core[0].add_message(core[2], 'user', text)
    return asyncio.run(core[1].extract(llm_output({'claims':candidates}),
                       ExtractionRequest(message_id=message['id'], **kw)))


def test_default_and_inherited_interview_preferences_are_separate_pending(core):
    text = '我平时喜欢用中文回答，不过面试模拟时希望用英文，而且回答尽量简洁。'
    candidates = [{'predicate':p,'value':v,'evidence_quote':text,'modality':m} for p,v,m in
                  [('response_language','中文','statement'),('response_language','英语','plan'),('response_detail','简短','statement')]]
    result = extract(core, text, candidates, infer_conditions=True)
    cn, en, detail = result['candidates']
    assert cn['scope']=={} and cn['value']=='中文'
    assert en['scope']==detail['scope']=={'activity':'interview'}
    assert en['value']=='英文' and en['modality']=='statement' and detail['value']=='简洁'
    assert all(c['status']=='pending' for c in result['candidates'])
    assert detail['condition_evidence'][0]['quote']=='面试模拟'
    assert result['automatic_approvals']==0
    assert all(not EvidenceVerifier().verify(core[1],c)['blockers'] for c in result['candidates'])


def test_short_quote_inherits_source_context_and_revocation_invalidates_it(core):
    text = '面试模拟时用英文，而且回答尽量简洁。'
    result = extract(core, text, [{'predicate':'response_detail','value':'简洁','evidence_quote':'回答尽量简洁'}],infer_conditions=True)
    claim = result['candidates'][0]
    assert claim['scope']=={'activity':'interview'} and claim['condition_evidence'][0]['quote']=='面试模拟'
    assert not EvidenceVerifier().verify(core[1],claim)['blockers']
    core[0].db.execute('UPDATE messages SET content=? WHERE id=?',('source was changed',claim['support_group_ids'][0]))
    core[0].db.commit()
    assert not core[1].get(claim['id'])['source_available']


@pytest.mark.parametrize('text', ['如果我疲劳就用英文','只有老板在的时候用英文','面试模拟时用英文，平时也用英文', 'Use English if I am tired'])
def test_unsupported_or_mixed_value_conditions_reject_without_partial_writes(core,text):
    with pytest.raises(ClaimError):
        extract(core,text,[{'predicate':'response_language','value':'英文','evidence_quote':text}],infer_conditions=True)
    assert core[1].list()==[]


def test_temporary_scope_has_source_span_but_no_invented_expiry(core):
    text = '这次面试模拟时用英文，而且尽量简洁'
    result=extract(core,text,[{'predicate':'response_detail','value':'简洁','evidence_quote':'尽量简洁'}],infer_conditions=True)
    claim=result['candidates'][0]
    assert claim['scope']=={'activity':'interview','temporary':True}
    assert '这次' in claim['condition_evidence'][0]['quote']
    assert claim['fact_end'] is None and not EvidenceVerifier().verify(core[1],claim)['blockers']


@pytest.mark.parametrize('date,precision,start', [('2026年10月','month',None),('2026年','year',None),
    ('2026年10月12日','day','2026-10-11T16:00:00.000000+00:00'),('2026-10-12T12:00:00+08:00','exact','2026-10-12T04:00:00.000000+00:00')])
def test_event_precision_and_no_month_first_day_state(core,date,precision,start):
    text=date+'我搬到上海'
    claim=extract(core,text,[{'predicate':'residence','value':'上海','evidence_quote':text}],infer_time=True)['candidates'][0]
    assert claim['event_time']['precision']==precision
    assert claim['fact_start']==start
    assert claim['time_precision']==(precision if start else 'unknown')


def test_other_sentence_date_is_not_this_facts_time(core):
    text='2026年10月12日我喜欢咖啡。我住上海'
    claim=extract(core,text,[{'predicate':'residence','value':'上海','evidence_quote':text}],infer_time=True)['candidates'][0]
    assert claim['event_time'] is None and claim['fact_start'] is None


@pytest.mark.parametrize('text', ['2026年2月30日我搬到上海','去年我搬到上海','2025年和2026年我住上海'])
def test_ambiguous_event_time_stays_unknown(text):
    assert TemporalResolver().assertion_fields(text)['event_time'] is None


def test_tampered_event_metadata_rejected(core):
    text='2026年10月我搬到上海'
    msg=core[0].add_message(core[2],'user',text)
    event=TemporalResolver().resolve(text).model_dump(); event['lower']='2026-10-02T00:00:00+08:00'
    with pytest.raises(ClaimError):
        core[1].create(ClaimDraft(predicate='residence',value='上海',event_time=event,
            evidence=[dict(message_id=msg['id'],start=0,end=len(text),quote=text,content_sha256=sha(text))]))
    assert core[1].list()==[]


def test_both_query_axes_use_intervals_and_transition_anchors():
    parser=TemporalQueryParser(); reference=datetime(2026,10,12,tzinfo=timezone.utc)
    fact=parser.parse('我去年住在哪里？',reference)
    recorded=parser.parse('上个月的时候，你认为我住在哪里？',reference)
    assert fact['temporal_intent']=='historical_fact' and fact['fact_interval']['lower'].startswith('2025-01-01')
    assert recorded['temporal_intent']=='historical_knowledge' and recorded['recorded_interval']['upper'].startswith('2026-10-01')
    assert not recorded['requires_clarification'] and recorded['recorded_as_of'] is None
    transition=parser.parse('我什么时候从南京搬到上海的？',reference)
    assert (transition['transition_from'],transition['transition_to'])==('南京','上海')
    assert transition['time_precision']=='unknown'


def test_recorded_range_baseline_and_changes_exclude_future(core,monkeypatch):
    import memoria.claims as module
    def approved(at,value,previous=None):
        monkeypatch.setattr(module,'now',lambda:at)
        text='我住'+value; msg=core[0].add_message(core[2],'user',text)
        evidence=[dict(message_id=msg['id'],start=0,end=len(text),quote=text,content_sha256=sha(text))]
        c=(core[1].revise(previous['id'],ClaimRevision(expected_revision=previous['revision'],value=value,evidence=evidence)) if previous else
           core[1].create(ClaimDraft(predicate='residence',value=value,evidence=evidence)))
        return core[1].approve(c['id'],ClaimDecision(expected_revision=c['revision'],reason='Engineering approval'))
    old=approved('2026-08-15T00:00:00.000000+00:00','南京')
    later=approved('2026-09-15T00:00:00.000000+00:00','上海',old)
    approved('2026-10-05T00:00:00.000000+00:00','北京',later)
    parsed=TemporalQueryParser().parse('上个月你认为我住哪里',datetime(2026,10,12,tzinfo=timezone.utc))
    timeline=MemoryAccessOrchestrator(core[1],None).timeline(QueryRequest(query='上个月你认为我住哪里'),parsed)
    assert {c['value'] for c in timeline}=={'南京','上海'}
    assert not any('北京' in canonical(c) for c in timeline)


def candidate(mid,text,score,covers):
    return dict(id=mid,kind='source',score=score,covers=covers,source_ids=[mid],data={'text':text})


@pytest.mark.parametrize('strategy',['topk','coverage','mmr','evidence_coverage'])
def test_complete_budget_trace_and_same_fixed_pool(strategy):
    pool=[candidate('a','南京 南京 南京',3,['from']),candidate('b','南京 南京 南京',2,['from']),candidate('c','上海',1,['to'])]
    result=select_evidence(pool,['from','to'],800,strategy,2)
    assert result['cost_chars']<=800 and len(result['trace'])==3
    assert set(result['candidate_ids'])=={t['id'] for t in result['trace']}
    assert not result['semantic_sufficiency_verified']
    if strategy in {'coverage','mmr','evidence_coverage'}:
        assert 'c' in {c['id'] for c in result['payload']['evidence']}


def test_byte_and_actual_token_budget_do_not_fake_remote_tokens():
    pool=[candidate('one','工程中文',1,['x'])]
    byte_budget=EvidenceBudget(600,'utf8_bytes')
    result=select_evidence(pool,['x'],600,budget=byte_budget)
    assert result['cost']==len(canonical(result['payload']).encode())
    assert not result['budget']['exact_reader_tokens']
    with pytest.raises(ValueError): EvidenceBudget(600,'reader_tokens')
    tokenizer=lambda text:list(text)
    actual=select_evidence(pool,['x'],600,budget=EvidenceBudget(600,'reader_tokens',tokenizer))
    assert actual['cost']==len(tokenizer(canonical(actual['payload']))) and actual['budget']['exact_reader_tokens']


def test_requirements_do_not_depend_on_gold_or_source_labels():
    query='我什么时候从南京搬到上海的？'
    parsed=TemporalQueryParser().parse(query)
    required=requirements_for(query,parsed)
    assert set(required['facets'])=={'slot:residence','time:fact','transition:from','transition:to'}
    assert required['origin']=='query_and_parser_only'


def test_rule_model_cannot_remove_ambiguity_or_invent_timestamps(core):
    llm=llm_output({'slots':[{'predicate':'residence','evidence_quote':'住哪里'}]})
    result=asyncio.run(MemoryAccessOrchestrator(core[1],MemoryEngine(core[0]),llm).access(
        QueryRequest(query='我朋友去年和今年住哪里',use_query_model=True)))
    assert result['path']=='clarify' and result['routing']['model_calls']==1
    assert result['automatic_approvals']==0 and result['reader_called'] is False


def test_model_extra_time_fields_and_unbound_quotes_rejected(core):
    for output in [{'slots':[], 'fact_as_of':'2026-01-01'}, {'slots':[{'predicate':'residence','evidence_quote':'not in query'}]}]:
        with pytest.raises(ClaimError):
            asyncio.run(MemoryAccessOrchestrator(core[1],None,llm_output(output)).access(QueryRequest(query='我的住所',use_query_model=True)))


def test_remote_reader_token_budget_explicitly_unavailable(core):
    with pytest.raises(ClaimError) as error:
        asyncio.run(MemoryAccessOrchestrator(core[1],None).access(QueryRequest(query='我的住所',budget_unit='reader_tokens')))
    assert error.value.status==422


@pytest.mark.parametrize('query',['如果累了我用什么语言回答','工作时和学习时我去年用什么语言回答','平时工作时我用什么语言回答'])
def test_explicit_time_cannot_clear_independent_condition_ambiguity(core,query):
    result=asyncio.run(MemoryAccessOrchestrator(core[1],MemoryEngine(core[0])).access(
        QueryRequest(query=query,fact_as_of='2026-01-01T00:00:00Z')))
    assert result['path']=='clarify' and result['parsed']['condition_requires_clarification']


def test_explicit_recorded_axis_reads_known_state_without_inventing_fact_time(core):
    claim=extract(core,'我用中文回答',[{'predicate':'response_language','value':'中文','evidence_quote':'我用中文回答'}])['candidates'][0]
    claim=core[1].approve(claim['id'],ClaimDecision(expected_revision=claim['revision'],reason='Engineering approval'))
    result=asyncio.run(MemoryAccessOrchestrator(core[1],None).access(QueryRequest(query='我的回答语言',recorded_as_of=claim['recorded_at'])))
    assert result['path']=='history' and result['parsed']['axis']=='recorded'
    assert result['selection']['uncovered']==[]
    assert result['selection']['payload']['evidence'][0]['data'][0]['fact_start'] is None
