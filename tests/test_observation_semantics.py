"""Explicitly synthetic contract/fault tests; not extraction F1 or longitudinal evidence."""
import asyncio
import json
import sqlite3
from datetime import datetime,timedelta,timezone

import pytest

from memoria.memory import observation_semantics as sem
from memoria.memory.temporal_resolver import TemporalResolver
from memoria.memory.temporal_query import TemporalQueryParser
from memoria.memory.access import QueryRequest
from memoria.memory.library import SourceInput,RevisionInput
from memoria.memory.records import MemorySearch
from memoria.memory.source_formation import FormRequest
from memoria.provenance import message_ingress
from tests.test_memory_formation import env,model,observer,send
from tests.test_source_formation import model as source_model

REF=datetime.fromisoformat('2023-01-02T00:30:00+08:00')


@pytest.mark.parametrize('text,precision,lower,upper',[
    ('我昨天归档项目。','day','2023-01-01','2023-01-02'),
    ('我前天归档项目。','day','2022-12-31','2023-01-01'),
    ('我今天归档项目。','day','2023-01-02','2023-01-03'),
    ('我明天归档项目。','day','2023-01-03','2023-01-04'),
    ('我后天归档项目。','day','2023-01-04','2023-01-05'),
    ('我上周归档项目。','range','2022-12-26','2023-01-02'),
    ('我本周归档项目。','range','2023-01-02','2023-01-09'),
    ('我下周归档项目。','range','2023-01-09','2023-01-16'),
    ('我去年归档项目。','year','2022-01-01','2023-01-01'),
    ('我前年归档项目。','year','2021-01-01','2022-01-01'),
    ('我三年前归档项目。','year','2020-01-01','2021-01-01'),
    ('我十二年前归档项目。','year','2011-01-01','2012-01-01'),
    ('我上个月归档项目。','month','2022-12-01','2023-01-01'),
    ('我下个月归档项目。','month','2023-02-01','2023-03-01'),
    ('我在2020年归档项目。','year','2020-01-01','2021-01-01'),
    ('我在2020年2月归档项目。','month','2020-02-01','2020-03-01'),
    ('我在2020年2月29日归档项目。','day','2020-02-29','2020-03-01'),
    ('我在2020年2月到2020年3月归档项目。','range','2020-02-01','2020-04-01'),
    ('I archived it yesterday.','day','2023-01-01','2023-01-02'),
    ('I archived it last week.','range','2022-12-26','2023-01-02'),
])
def test_shared_calendar_intervals(text,precision,lower,upper):
    event=TemporalResolver().resolve_event(text,REF)['event_time']
    assert event.precision==precision and event.lower.startswith(lower) and event.upper.startswith(upper)
    assert event.lower.endswith('+08:00') and event.quote in text


@pytest.mark.parametrize('text,reason',[
    ('我昨天归档项目。','missing_original_source_time'),
    ('我上周归档项目。','missing_original_source_time'),
    ('我2023年2月29日归档项目。','invalid_calendar_date'),
    ('我2023年13月归档项目。','invalid_calendar_date'),
    ('我2022年启动，2023年归档。','multiple_time_expressions'),
    ('我2023年昨天归档项目。','multiple_time_expressions'),
    ('我在2023年到2021年归档项目。','invalid_calendar_range'),
    ('我上次归档项目。','unsupported_time_expression'),
    ('我在2023年5月到7月归档项目。','unsupported_range_endpoint'),
    ('我在2023-01-01T10:00:00归档项目。','missing_exact_timezone'),
    ('我归档了项目。','no_explicit_event_time'),
])
def test_unknown_dates_are_not_invented(text,reason):
    result=TemporalResolver().resolve_event(text)
    assert result['event_time'] is None and result['reason']==reason


def process(env,text,*,source_time=None,**changes):
    store,service,_=env
    model(service,specs={text:observer(text,**({'category':'fact','content':text,'asserted':True,'durable':True}|changes))})
    source=service.kernel.record_message(store.create_session()['id'],'user',text,source_time=source_time)
    job=store.enqueue_memory_job(source['id'],text,'')
    assert asyncio.run(service.memory_worker.process_once())
    assert store.memory_job(job['id'])['status']=='completed'
    row=store.db.execute('SELECT * FROM memory_observations WHERE message_id=?',(source['id'],)).fetchone()
    return source,dict(row),sem.load(store,'conversation',row['id'])


def test_retrospective_message_has_three_distinct_clocks(env):
    source,row,data=process(env,'我昨天完成星桥项目的归档。',source_time=REF.isoformat())
    assert data['subject_ref']=='self' and data['source_role']=='user'
    assert data['event_time']['lower'].startswith('2023-01-01')
    assert data['source_time']==REF.isoformat() and data['source_time_basis']=='declared_original'
    assert data['source_recorded_at']==source['created_at']==row['observed_at']
    assert data['recorded_at']>=source['created_at']>data['source_time']
    assert env[2].get('/api/memory-observations').json()[0]['semantics']==data
    assert data['evidence_ref'][0]['source_sha256']==row['source_sha']


@pytest.mark.parametrize('channel',['chat','api','feishu','qq','telegram'])
def test_live_channels_share_the_same_source_clock(env,channel):
    original=REF.isoformat() if channel in {'feishu','qq','telegram'} else None
    with message_ingress(channel,source_time=original):source,row,data=process(env,'我昨天完成星桥项目的归档。')
    expected=datetime.fromisoformat(data['source_time']).date()-timedelta(days=1)
    assert data['source_time_basis']==('platform_event' if original else 'live_received') and data['event_time']['lower'].startswith(str(expected))
    assert data['source_recorded_at']==source['created_at'] and data['event_time']['lower']!=source['created_at']


def test_internal_or_legacy_source_time_is_unknown(env):
    source,row,data=process(env,'我昨天完成星桥项目的归档。')
    assert data['event_time'] is None and data['source_time'] is None
    assert data['time_reason']=='missing_original_source_time'
    store=env[0]
    with store._memory_transaction():
        sid=store.create_session()['id']
        store.db.execute('INSERT INTO messages VALUES(?,?,?,?,?)',('legacy',sid,'user','昨天的历史内容',REF.isoformat()))
    raw=store.message_source('legacy')
    assert sem.message_clock(store,raw)['basis']=='legacy_unknown'
    assert sem.message_clock(store,raw)['source_time'] is None


def test_temporal_context_is_local_and_still_literal(env):
    source,row,data=process(env,'2020年2月，我完成星桥项目归档。',quote='我完成星桥项目归档',temporal_quote='2020年2月，我完成星桥项目归档')
    assert data['time_precision']=='month' and data['event_time']['lower'].startswith('2020-02-01')
    assert {e['purpose'] for e in data['evidence_ref']}=={'fact','event_time'}
    assert all(source['content'][e['start']:e['end']]==e['quote'] for e in data['evidence_ref'])


@pytest.mark.parametrize('text,context',[
    ('2020年总结。\n我完成星桥项目归档。','2020年总结。\n我完成星桥项目归档'),
    ('2020年我启动项目；我完成星桥项目归档。','2020年我启动项目；我完成星桥项目归档'),
    ('我完成星桥项目归档。','2020年我完成星桥项目归档'),
])
def test_cannot_borrow_header_neighbor_or_model_date(env,text,context):
    store,service,_=env
    source=store.add_message(store.create_session()['id'],'user',text)
    with pytest.raises(ValueError):sem.build(store,'conversation',source,source,
        observer(text,quote='我完成星桥项目归档',temporal_quote=context))


@pytest.mark.parametrize('text,expected',[
    ('我计划2023年完成星桥项目归档。','planned'),
    ('假设我在2023年完成星桥项目归档。','hypothetical'),
    ('我2023年没有完成星桥项目归档。','negated'),
])
def test_plans_hypotheses_and_negation_keep_their_status(env,text,expected):
    polarity='negative' if expected=='negated' else 'positive'
    _,_,data=process(env,text,polarity=polarity)
    assert data['event_status']==expected
    assert data['time_precision']=='year' and data['event_time']['quote']=='2023年'
    assert data['epistemic_status']!='tool_verified'


def test_third_party_never_becomes_self_even_if_model_claims_durable(env):
    _,row,data=process(env,'我同事昨天完成星桥项目归档。',source_time=REF.isoformat())
    assert data['subject_ref']=='unknown' and row['durable']==0 and row['asserted']==0
    assert env[0].db.execute('SELECT count(*) FROM memory_reviews').fetchone()[0]==0


@pytest.mark.parametrize('operation',[
    "UPDATE memory_source_clocks SET source_time='2000-01-01T00:00:00+08:00'",
    'DELETE FROM memory_source_clocks',
    "UPDATE memory_observation_semantics SET data_json='{}'",
    'DELETE FROM memory_observation_semantics',
])
def test_semantic_receipts_and_clocks_cannot_be_replaced(env,operation):
    process(env,'我在2023年完成星桥项目归档。')
    with pytest.raises(sqlite3.IntegrityError),env[0]._memory_transaction():env[0].db.execute(operation)


def add_source(env,text,source_time=None):
    service=env[1];source_model(service)
    item=service.kernel.ingest(SourceInput(kind='document',title='资料',content=text,authorship='external',source_time=source_time))
    result=asyncio.run(service.library.formation.form(item['id'],FormRequest()))
    oid=result['observations'][0]['id']
    return item,sem.load(env[0],'library',oid)


@pytest.mark.parametrize('original',['2023-01-02T00:30:00+08:00',None])
def test_late_document_upload_never_uses_upload_time(env,original):
    item,data=add_source(env,'Memoria昨天完成工程检索配置校验。',original)
    assert data['source_role']=='external' and data['epistemic_status']=='source_assertion'
    assert data['subject_ref'].startswith('source:'+item['id']) and data['subject_ref']!='self'
    assert data['source_recorded_at']==item['revision']['created_at']
    assert data['recorded_at']>=item['revision']['created_at']
    if original:assert data['event_time']['lower'].startswith('2023-01-01')
    else:assert data['event_time'] is None and data['source_time'] is None


def test_same_document_name_does_not_globally_merge_subjects(env):
    a,first=add_source(env,'Memoria在2020年完成工程检索配置校验。')
    b,second=add_source(env,'Memoria在2021年完成工程检索配置校验。')
    assert first['subject_ref']!=second['subject_ref'] and a['id']!=b['id']


def test_native_source_revisions_do_not_inherit_original_time(env):
    item,data=add_source(env,'Memoria昨天完成工程检索配置校验。',REF.isoformat())
    library=env[1].library
    revised=library.revise(item['id'],RevisionInput(expected_revision_id=item['revision']['id'],
        title='新版资料',content='Memoria昨天完成了新版工程检索配置校验。'))
    assert revised['source_clock']['source_time'] is None
    assert library.version(item['id'],item['revision']['id'])['created_at']==item['revision']['created_at']


def test_event_and_recorded_filters_are_used_by_native_search(env):
    item,data=add_source(env,'Memoria在2023年1月完成工程检索配置校验。')
    kernel=env[1].kernel
    def search(**kw):return asyncio.run(kernel.search(MemorySearch(query='Memoria工程检索配置校验',domains=['library'],**kw)))
    result=search(event_start='2023-01-01T00:00:00+08:00',event_end='2023-02-01T00:00:00+08:00')
    assert result['evidence'] and all(r['provenance']['observations'] for r in result['evidence'])
    assert not search(event_start='2024-01-01T00:00:00+08:00',event_end='2024-02-01T00:00:00+08:00')['evidence']
    assert not search(recorded_as_of='2024-01-01T00:00:00+08:00')['evidence']
    assert not search(subject_ref='self')['evidence']
    snapshot=result['dependencies'][0]
    assert kernel.dependency_valid(snapshot)
    kernel.library.retract(item['id'])
    assert not kernel.dependency_valid(snapshot)


def test_ordinary_history_query_uses_event_time_not_memory_publication(env):
    source,row,data=process(env,'我在2023年1月完成星桥项目归档。')
    kernel=env[1].kernel
    async def query(text):return await kernel.access(QueryRequest(query=text,mode='bm25',include_experiences=False))
    result=asyncio.run(query('2023年1月我完成星桥项目的什么工作？'))
    assert result['parsed']['temporal_intent']=='historical_fact' and result['path']=='retrieve'
    assert result['candidates'] and result['candidates'][0]['data']['observations'][0]['event_time']['precision']=='month'
    wrong=asyncio.run(query('2024年1月我完成星桥项目的什么工作？'))
    assert not wrong['candidates']


def test_coarse_claim_dates_are_still_not_exact_fact_starts():
    result=TemporalResolver().assertion_fields('2023年我住在南京。')
    assert result['event_time'].precision=='year' and result['fact_start'] is None and result['time_precision']=='unknown'
    parsed=TemporalQueryParser().parse('上周我完成什么工作？',REF)
    assert parsed['fact_interval']['lower'].startswith('2022-12-26') and not parsed['requires_clarification']


def test_exact_timestamp_keeps_microsecond_precision_and_original_offset():
    event=TemporalResolver().resolve_event('归档于2023-01-01T23:59:59.500000-05:00')['event_time']
    assert event.precision=='exact' and event.lower.endswith('-05:00')
    assert datetime.fromisoformat(event.upper)-datetime.fromisoformat(event.lower)==timedelta(microseconds=1)
    with pytest.raises(ValueError):TemporalResolver().resolve_event('昨天',datetime(2023,1,2))


def test_registered_entity_subject_is_stable_but_revocation_is_fresh(env):
    from tests.test_entities import register
    entity,_=register(env[1].kernel,name='星桥项目')
    source,row,data=process(env,'星桥项目在2023年完成归档。')
    assert data['subject_ref']=='entity:'+entity['id']
    assert sem.source_observations(env[0],'conversation',source['id'])
    env[1].kernel.entities.revoke(entity['id'],entity['revision'])
    assert not sem.source_observations(env[0],'conversation',source['id'])


def test_condition_binding_remains_separate_evidence(env):
    source,row,data=process(env,'在办公室时，我昨天完成星桥项目归档。',source_time=REF.isoformat(),quote='我昨天完成星桥项目归档')
    assert data['condition_scope']['constraints']=={'location':'office'}
    assert data['condition_scope']['status']=='scoped'
    assert any(e['purpose']=='condition' and e['quote']=='在办公室时' for e in data['evidence_ref'])


def test_tool_reports_cannot_claim_verified_user_facts(env):
    store,service,_=env
    source=store.add_message(store.create_session()['id'],'tool','我在2023年完成项目归档。')
    data=sem.build(store,'conversation',source,source,observer(source['content'],asserted=True))
    assert data['source_role']=='tool' and data['subject_ref']=='unknown' and data['epistemic_status']!='tool_verified'
    with pytest.raises(ValueError):
        from memoria.memory.formation_store import eligible_source
        eligible_source(store,source['id'])


def test_file_upload_source_time_is_explicit_validated_and_never_guessed(env):
    _,service,client=env
    endpoint='/api/library/import?filename=report.txt&authorship=external'
    response=client.post(endpoint+'&source_time=2023-01-02T00%3A30%3A00%2B08%3A00',content='Memoria昨天完成校验。'.encode())
    assert response.status_code==201 and response.json()['source_clock']['source_time']==REF.isoformat()
    assert client.post(endpoint+'&source_time=2023-01-02',content=b'text').status_code==422
    unknown=client.post(endpoint,content=b'old document')
    assert unknown.status_code==201 and unknown.json()['source_clock']['source_time'] is None


def test_event_receipts_never_publish_current_state_or_hypothetical_completion(env):
    _,_,data=process(env,'假设我在2023年完成星桥项目归档。')
    assert env[0].memories()==[] and env[1].claims.list()==[] and data['event_status']=='hypothetical'


def test_explicit_plan_can_be_a_bounded_memory_without_becoming_completion(env):
    _,_,data=process(env,'我计划2027年完成星桥项目归档。',category='goal')
    assert data['event_status']=='planned' and env[1].claims.list()==[]
    assert env[0].memories() and all('计划' in m['content'] for m in env[0].memories())


def test_known_document_date_needs_independent_time_support(env):
    from memoria.memory.source_formation import VERIFY
    def alter(stage,data,output,_):
        if stage==VERIFY:
            for verdict in output['verdicts']:verdict.pop('time_supported',None)
    service=env[1];source_model(service,alter=alter)
    item=service.kernel.ingest(SourceInput(kind='document',title='时间反例',authorship='external',
        content='Memoria在2023年完成工程检索配置校验。'))
    receipt=asyncio.run(service.library.formation.form(item['id'],FormRequest()))
    assert receipt['observations'][0]['status']=='needs_assistance'
    verdict=json.loads(service.library.detail(item['id'])['cards'][0]['verdict_json'])
    assert 'event_time_semantic_support_not_verified' in verdict['admission_reasons']


def test_selected_observation_withdrawal_invalidates_reader_dependency(env):
    from memoria.memory.temporal_evidence import references,valid
    source,row,data=process(env,'我在2023年1月完成星桥项目归档。')
    refs=references(sem.source_observations(env[0],'conversation',source['id']),'conversation')
    assert refs and valid(env[0],refs)
    env[0].undo_memory_sources([source['id']])
    assert not valid(env[0],refs)


def test_clock_cleanup_follows_original_source_deletion(env):
    source,row,data=process(env,'我在2023年完成星桥项目归档。')
    with env[0]._memory_transaction():env[0].db.execute('DELETE FROM messages WHERE id=?',(source['id'],))
    assert not env[0].db.execute('SELECT 1 FROM memory_source_clocks WHERE revision_ref=?',(source['id'],)).fetchone()
    assert sem.load(env[0],'conversation',row['id']) is None


@pytest.mark.parametrize('query',[
    '2023年3月我交付过什么模块？','上周我完成了哪些任务？','我以前完成过什么项目？',
])
def test_chat_routes_event_history_through_the_temporal_reader(query):
    from memoria.memory.access import infer_query
    assert isinstance(infer_query(query),QueryRequest)


@pytest.mark.parametrize('query',['我去年没完成项目，现在该怎么办？','我昨天拉肚子为什么？','帮我写一个项目计划'])
def test_general_advice_still_uses_normal_chat(query):
    from memoria.memory.access import infer_query
    assert infer_query(query) is None


def test_ordinary_chat_frame_and_tool_recall_keep_event_metadata(env):
    from memoria.prompting.assembler import PromptAssembler
    from memoria.memory.engine import MemoryEngine
    source,row,data=process(env,'我在2023年1月完成星桥项目归档。')
    items=asyncio.run(env[1].runtime.memory.retrieve('星桥项目归档',mode='bm25'))
    assert items and items[0]['observation_semantics'][0]['event_time']['precision']=='month'
    frame=PromptAssembler._format_memories(items)
    assert '2023-01-01' in frame and 'source_time' in frame and 'recorded_at' in frame
    dependency=items[0]['retrieval']['dependency']
    assert env[0].memory_dependency_valid(dependency)
    with env[0]._memory_transaction():env[0].db.execute("UPDATE memory_observations SET state='archived' WHERE id=?",(row['id'],))
    assert env[0].memory_dependency_valid(dependency)  # Consolidation retention isn't evidence withdrawal.
    assert sem.source_observations(env[0],'conversation',source['id'])
    env[0].undo_memory_sources([source['id']])
    assert not env[0].memory_dependency_valid(dependency)


def test_native_memory_domain_with_semantics_still_passes_dependency_equality(env):
    process(env,'我在2023年1月完成星桥项目归档。')
    result=asyncio.run(env[1].kernel.search(MemorySearch(query='星桥项目归档',domains=['memory'],memory_mode='bm25')))
    assert result['evidence'] and all(env[1].kernel.dependency_valid(d) for d in result['dependencies'])


def test_hypothetical_without_date_still_cannot_publish(env):
    process(env,'假设我完成了星桥项目归档。')
    assert env[0].memories()==[] and env[1].claims.list()==[]


def test_subject_and_modality_are_separate_consolidation_buckets(env):
    from memoria.memory.formation_store import with_entities,bucket
    _,a,_=process(env,'我在2023年完成星桥项目归档。')
    _,b,_=process(env,'我同事在2023年完成星桥项目归档。')
    _,c,_=process(env,'我计划2027年完成星桥项目归档。')
    keys=[bucket(with_entities(env[0],r)) for r in (a,b,c)]
    assert len(set(keys))==3


def test_literal_failure_cannot_be_labeled_a_completed_plan(env):
    _,row,data=process(env,'我计划2023年交付星桥模块，但没有完成交付。')
    assert data['event_status']=='negated'


def test_bot_missing_source_timestamp_is_unknown_and_assistant_does_not_inherit_user_time(env):
    store=env[0];sid=store.create_session()['id']
    with message_ingress('telegram',source_time=REF.isoformat()):
        user=store.add_message(sid,'user','昨天的经历')
        assistant=store.add_message(sid,'assistant','回复')
        with message_ingress('api',preserve=True):preserved=store.add_message(sid,'user','继续')
    assert sem.message_clock(store,user)['basis']=='platform_event'
    assert sem.message_clock(store,preserved)['source_time']==REF.isoformat()
    assert sem.message_clock(store,assistant)['source_time'] is None
    with message_ingress('qq'):unknown=store.add_message(sid,'user','昨天的经历')
    assert sem.message_clock(store,unknown)['source_time'] is None


def test_missing_polarity_has_one_audited_repair_without_positive_default(env):
    store,service,_=env;text='我在2023年没有完成蓝杉模块交付。'
    def mutate(stage,output,data):
        if stage=='observe' and 'schema_repair' not in data:
            output['observations']=[dict(r) for r in output['observations']]
            output['observations'][0].pop('polarity')
    calls=model(service,specs={text:observer(text,category='experience',content=text,asserted=True,durable=True,polarity='negative')},mutate=mutate)
    source,job=send(store,service,text)
    assert job['status']=='completed' and calls==['observe','observe','review']
    row=store.db.execute('SELECT * FROM memory_observations').fetchone()
    assert row['polarity']=='negative' and '没有' in store.memories()[0]['content']
    audit=store.db.execute('SELECT stage,status FROM memory_formation_calls ORDER BY rowid').fetchall()
    assert [tuple(r) for r in audit]==[('observe','invalid_output'),('observe_repair','returned')]
    diagnostics=[json.loads(r[0]) for r in store.db.execute('SELECT diagnostics_json FROM memory_extraction_diagnostics ORDER BY rowid')]
    assert diagnostics[0]['status']=='schema_repair_requested' and diagnostics[-1]['schema_repair']['attempts']==1


@pytest.mark.parametrize('bad_repair',['still_missing','forged_quote'])
def test_schema_repair_is_bounded_and_cannot_bypass_source_checks(env,bad_repair):
    store,service,_=env;text='我在2023年完成蓝杉模块交付。'
    def mutate(stage,output,data):
        if stage=='observe':
            output['observations']=[dict(r) for r in output['observations']]
            if 'schema_repair' not in data or bad_repair=='still_missing':output['observations'][0].pop('polarity')
            else:output['observations'][0]['quote']='没有在来源中的内容'
    calls=model(service,specs={text:observer(text,category='experience',content=text,asserted=True,durable=True)},mutate=mutate)
    _,job=send(store,service,text)
    assert job['status']!='completed' and calls==['observe','observe']
    assert not store.memories() and not service.claims.list()


def test_partial_valid_batch_is_not_reextracted_to_repair_another_row(env):
    store,service,_=env;text='我维护蓝杉模块。我维护青石模块。'
    def mutate(stage,output,data):
        if stage=='observe':
            valid=observer('我维护蓝杉模块。',category='fact',content='我维护蓝杉模块。',asserted=True,durable=True)
            invalid=observer('我维护青石模块。',category='fact',content='我维护青石模块。',asserted=True,durable=True)
            invalid.pop('polarity');output['observations']=[valid,invalid]
    calls=model(service,mutate=mutate);_,job=send(store,service,text)
    assert job['status']=='completed' and calls==['observe','review']
    assert store.db.execute('SELECT count(*) FROM memory_observations').fetchone()[0]==1


def test_unparsed_free_text_condition_is_not_unconditional(env):
    _,_,data=process(env,'我在故障恢复时使用快照回滚。',scope='故障恢复时')
    assert data['condition_scope']['label']=='故障恢复时' and data['condition_scope']['status']=='unknown'


def test_coarse_year_cannot_prove_an_event_in_a_particular_month(env):
    _,_,data=process(env,'我在2023年完成蓝杉模块交付。')
    assert not sem.matches(data,event_start='2023-03-01T00:00:00+08:00',event_end='2023-04-01T00:00:00+08:00')
    assert sem.matches(data,event_start='2023-01-01T00:00:00+08:00',event_end='2024-01-01T00:00:00+08:00')
    request=QueryRequest(query='2023年3月我完成什么模块？',mode='bm25',include_experiences=False)
    answer=asyncio.run(env[1].kernel.access(request))
    assert not answer['candidates'] and answer['path']=='clarify'


@pytest.mark.parametrize('field,value',[
    ('source_time','2023-01-01'),('source_time','2023-01-01T00:00:00'),
])
def test_source_date_requires_original_timezone(field,value):
    with pytest.raises(ValueError):SourceInput(kind='note',title='资料',content='原文',**{field:value})
