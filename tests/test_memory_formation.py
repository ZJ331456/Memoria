"""Temporary engineering sources verify formation gates, not real-world effect sizes."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from memoria.api.app import _create_tenant_app
from memoria.llm.types import ChatResult
from memoria.memory.formation_settings import FormationSettings
from memoria.memory.processing import message_states


@pytest.fixture
def env(tmp_path):
    path = tmp_path/'config.toml'
    path.write_text('[storage]\ndatabase="'+(tmp_path/'memory.db').as_posix()+'"\n'
        '[memory.markdown]\nenabled=false\n[memory.formation]\nenabled=true\n'
        '[llm.main]\nmodel="engineering-formation"\nbase_url="https://model.invalid/v1"\napi_key="engineering-only"\n', encoding='utf-8')
    app = _create_tenant_app(path)
    yield app.state.store, app.state.service, TestClient(app)
    app.state.store.close()


def observer(text, **changes):
    row = {'category':'interest','topic':'开发资料','scope':'','polarity':'positive',
           'content':'用户本次查阅开发资料：'+text,'quote':text,'asserted':False,'durable':False,'importance':3}
    return row | changes


def model(service, *, specs=None, decision='accept', action=None, content=None, mutate=None):
    calls=[]
    async def chat(messages, **kwargs):
        prompt=messages[0]['content']; data=json.loads(messages[1]['content'])
        if '个人记忆的观察器' in prompt:
            stage='observe'; text=data['source']
            output={'observations':[(specs or {}).get(text,observer(text))]}
        elif '跨会话记忆整理器' in prompt:
            stage='consolidate'
            output={'action':action or ('MERGE' if data['previous'] else 'ADD'),
                'content':content or '用户近期在多次对话中关注开发资料',
                'observation_ids':[r['id'] for r in data['observations']], 'reason':'跨会话原话共同支持的近期模式'}
        else:
            stage='review'; pattern=data.get('formation')=='pattern'
            sources=json.loads(data['source']) if pattern else []
            output={'decision':decision,'assessment':'Supported' if decision=='accept' else 'Insufficient',
                'durable':True,'self_related':True,'conditions_preserved':True,'time_supported':True,'sensitive':False,
                'evidence_quote':'' if pattern else data['source'],'claim':None,
                'supporting_quotes':[{'message_id':s['message_id'],'quote':s['content']} for s in sources]}
        calls.append(stage)
        if mutate: mutate(stage,output,data)
        return ChatResult(content=json.dumps(output,ensure_ascii=False),raw_message={},usage={'total_tokens':17})
    service.llm.chat=chat
    return calls


def send(store,service,text,session=None):
    source=store.add_message(session or store.create_session()['id'],'user',text)
    job=store.enqueue_memory_job(source['id'],text,'assistant text is never observation evidence')
    assert asyncio.run(service.memory_worker.process_once())
    return source,store.memory_job(job['id'])


def cohort(store,service):
    return [send(store,service,text)[0] for text in ('查找开发文章甲','搜索开发笔记乙','查阅开发教程丙')]


@pytest.mark.parametrize('channel',['chat','api','feishu','qq','telegram'])
def test_one_request_becomes_observation_not_preference(env,channel):
    from memoria.provenance import message_ingress
    store,service,client=env
    calls=model(service)
    with message_ingress(channel):
        source,job=send(store,service,'查找开发文章甲')
    assert job['status']=='completed' and job['extracted_count']==0
    assert store.memories()==[] and store.memory_reviews('all')==[]
    assert len(client.get('/api/memory-observations').json())==1
    assert message_states(store,[source['id']])[source['id']]['memory_processing']['state']=='observing'
    assert calls==['observe']
    assert message_states(store,[source['id']])[source['id']]['origin']['channel']==channel


def test_explicit_fact_without_preference_keywords_can_publish(env):
    store,service,_=env
    text='目前从事后端开发，主要维护数据库服务。'
    calls=model(service,specs={text:observer(text,category='fact',content='用户从事后端开发，主要维护数据库服务',asserted=True,durable=True)})
    _,job=send(store,service,text)
    assert job['status']=='completed' and job['extracted_count']==1
    assert len(store.memories())==1
    assert calls==['observe','review']


def test_cross_session_pattern_requires_separate_review_and_full_support(env):
    store,service,client=env
    calls=model(service)
    sources=cohort(store,service)
    assert calls==['observe','observe','observe','consolidate','review']
    memory=store.memories()[0]
    assert memory['source']=='consolidated_pattern'
    group=store.db.execute('SELECT id FROM memory_support_groups WHERE memory_id=?',(memory['id'],)).fetchall()
    assert len(group)==1
    assert store.db.execute('SELECT count(*) FROM memory_support_evidence WHERE group_id=?',(group[0][0],)).fetchone()[0]==3
    assert len(service.formation.core())==1
    assert len(client.get('/api/memory-patterns').json()[0]['evidence_message_ids'])==3
    with store.db:store.db.execute('UPDATE messages SET content=? WHERE id=?',('修改后的原话',sources[0]['id']))
    assert store.retrieval_memories()==[] and service.formation.core()==[]
    service.formation.maintenance()
    assert store.db.execute('SELECT status FROM memory_pattern_versions').fetchone()[0]=='retired'


@pytest.mark.parametrize('mode',['same_session','copied_source','different_scope','different_polarity'])
def test_repetition_alone_does_not_satisfy_formation(env,mode):
    store,service,_=env
    texts=['查找开发文章甲','搜索开发笔记乙','查阅开发教程丙']
    specs={texts[-1]:observer(texts[-1],**({'scope':'另一场景'} if mode=='different_scope' else {'polarity':'negative'}))} if mode.startswith('different') else None
    calls=model(service,specs=specs)
    session=store.create_session()['id'] if mode=='same_session' else None
    for text in texts:send(store,service,texts[0] if mode=='copied_source' else text,session)
    assert calls==['observe']*3 and store.memories()==[]


def test_second_model_may_refuse_a_plausible_pattern(env):
    store,service,_=env
    model(service,decision='review')
    cohort(store,service)
    assert store.memories()==[]
    assert store.memory_reviews()[0]['assessment']['decision']=='review'


def test_pattern_without_all_source_quotes_cannot_publish(env):
    store,service,_=env
    def mutate(stage,output,data):
        if stage=='review':output['supporting_quotes']=output['supporting_quotes'][:1]
    model(service,mutate=mutate)
    cohort(store,service)
    assert store.memories()==[]
    assert 'complete_pattern_evidence_not_verified' in store.memory_reviews()[0]['assessment']['reason_codes']


def test_observation_race_rolls_back_staged_candidates_and_keeps_call_cost(env):
    store,service,_=env
    text='从事后台开发。'
    def mutate(stage,output,data):
        if stage=='observe':
            with store.db:store.db.execute('UPDATE messages SET content=? WHERE content=?',('原话已改写',text))
    model(service,specs={text:observer(text,asserted=True,durable=True)},mutate=mutate)
    _,job=send(store,service,text)
    assert job['status']=='retry'
    assert store.db.execute('SELECT count(*) FROM memory_observations').fetchone()[0]==0
    assert store.memory_reviews('all')==[]
    assert json.loads(store.db.execute('SELECT usage_json FROM memory_formation_calls').fetchone()[0])['total_tokens']==17


def test_unknown_observation_ids_do_not_publish(env):
    store,service,_=env
    def mutate(stage,output,data):
        if stage=='consolidate':output['observation_ids'].append('not-a-source')
    model(service,mutate=mutate)
    cohort(store,service)
    assert store.memories()==[]
    assert store.db.execute("SELECT count(*) FROM memory_jobs WHERE status='completed'").fetchone()[0]==3
    assert store.db.execute("SELECT count(*) FROM memory_formation_events WHERE action='CONSOLIDATION_FAILED'").fetchone()[0]==1


def test_consolidation_network_failure_does_not_repeat_successful_extraction(env):
    store,service,_=env
    calls=model(service)
    working=service.llm.chat
    async def interrupted(messages,**kwargs):
        if '跨会话记忆整理器' in messages[0]['content']:
            raise TimeoutError('engineering timeout')
        return await working(messages,**kwargs)
    service.llm.chat=interrupted
    cohort(store,service)
    assert store.db.execute('SELECT count(*) FROM memory_observations').fetchone()[0]==3
    assert store.db.execute("SELECT count(*) FROM memory_jobs WHERE status='completed'").fetchone()[0]==3
    failure=store.db.execute("SELECT * FROM memory_formation_calls WHERE stage='consolidate'").fetchone()
    assert failure['status']=='provider_failed' and failure['usage_json'] is None
    assert not asyncio.run(service.memory_worker.process_once())
    assert calls==['observe']*3


def test_same_conclusion_reinforces_without_creating_duplicate_facts(env):
    store,service,_=env
    model(service)
    cohort(store,service)
    old=store.memories()[0]['id']
    _,job=send(store,service,'再查一份开发指南丁')
    assert job['status']=='completed'
    assert [m['id'] for m in store.memories()]==[old]
    assert store.db.execute("SELECT count(*) FROM memory_pattern_versions WHERE action='REINFORCE' AND status='active'").fetchone()[0]==1


def test_new_head_requires_its_own_complete_group_not_older_cohort(env):
    store,service,_=env
    model(service);cohort(store,service)
    newest,_=send(store,service,'再查一份开发指南丁')
    assert len(store.retrieval_memories())==1
    with store.db:store.db.execute('UPDATE messages SET content=? WHERE id=?',('撤回这次观察依据',newest['id']))
    assert store.retrieval_memories()==[]


def test_pending_pattern_cannot_publish_over_a_newer_approved_head(env):
    from memoria.claims import ClaimError
    store,service,_=env
    model(service,decision='review');cohort(store,service)
    pending=store.memory_reviews()[0]
    model(service)
    send(store,service,'再查一份开发指南丁')
    with pytest.raises(ClaimError):asyncio.run(service.auto_review.review(pending['id'],force=True))
    assert len(store.memories())==1


def test_explicit_reextraction_is_a_new_request_but_retry_is_not(env):
    store,service,_=env
    calls=model(service)
    source,job=send(store,service,'查找开发文章甲')
    with store.db:
        store.db.execute("UPDATE memory_jobs SET status='retry',available_at='2000-01-01T00:00:00+00:00' WHERE id=?",(job['id'],))
    assert asyncio.run(service.memory_worker.process_once())
    assert calls==['observe']
    store.request_memory_extraction(source['id'])
    assert asyncio.run(service.memory_worker.process_once())
    assert calls==['observe','observe']
    assert store.db.execute('SELECT count(*) FROM memory_formation_runs').fetchone()[0]==2


def test_changed_pattern_creates_immutable_version_and_preserves_history(env):
    store,service,_=env
    model(service)
    cohort(store,service)
    old=store.memories()[0]['id']
    model(service,action='UPDATE',content='用户近期在多次对话中持续查阅开发教程资料')
    _,job=send(store,service,'进一步查阅开发教程丁')
    assert job['status']=='completed'
    assert store.memory(old)['status']=='superseded'
    current=store.memories()[0]
    assert current['id']!=old and current['supersedes_id']==old
    assert len(store.memory_timeline(old))==2
    service.formation.maintenance()
    assert store.db.execute("SELECT count(*) FROM memory_pattern_versions WHERE status='active'").fetchone()[0]==1


def test_model_delete_is_only_an_assistance_proposal(env):
    store,service,_=env
    model(service);cohort(store,service)
    old=store.memories()[0]['id']
    model(service,action='DELETE')
    send(store,service,'进一步查阅开发教程丁')
    assert store.memory(old)['status']=='active'
    assert store.db.execute("SELECT count(*) FROM memory_pattern_versions WHERE status='needs_assistance'").fetchone()[0]==1
    assert store.db.execute("SELECT action FROM memory_pattern_versions WHERE status='needs_assistance'").fetchone()[0]=='DELETE'


def test_core_ages_from_evidence_not_late_reprocessing_time(env):
    from datetime import datetime,timedelta,timezone
    store,service,_=env
    model(service);cohort(store,service)
    # Creation stays current; evidence horizon is 60 days old: archival, not core.
    expiry=(datetime.now(timezone.utc)+timedelta(days=30)).isoformat()
    with store.db:store.db.execute('UPDATE memory_pattern_versions SET expires_at=?',(expiry,))
    assert service.formation.core()==[]
    assert len(store.retrieval_memories())==1


def test_unselected_recent_observation_cannot_refresh_older_pattern(env):
    from datetime import datetime, timedelta, timezone
    store,service,_=env
    recent='再查一份开发指南丁'
    def mutate(stage,output,data):
        if stage=='consolidate':
            output['observation_ids']=[r['id'] for r in data['observations'] if r['quote']!=recent]
    model(service,mutate=mutate)
    service.auto_review.mode='manual'
    observed=(datetime.now(timezone.utc)-timedelta(days=60)).isoformat()
    for text in ('查找开发文章甲','搜索开发笔记乙','查阅开发教程丙'):
        source=store.add_message(store.create_session()['id'],'user',text)
        with store.db:store.db.execute('UPDATE messages SET created_at=? WHERE id=?',(observed,source['id']))
        store.enqueue_memory_job(source['id'],text,'')
        assert asyncio.run(service.memory_worker.process_once())
    service.auto_review.mode='llm'
    source,_=send(store,service,recent)
    pattern=store.db.execute("SELECT * FROM memory_pattern_versions WHERE status='active'").fetchone()
    assert pattern is not None
    assert pattern['expires_at']==(datetime.fromisoformat(observed)+timedelta(days=90)).isoformat()
    from memoria.memory.formation_store import pattern_sources
    assert source['id'] not in {r['id'] for r in pattern_sources(store,pattern)}
    assert service.formation.core()==[]


def test_noop_has_source_provenance_and_hides_after_source_edit(env):
    store,service,client=env
    model(service,action='NOOP')
    sources=cohort(store,service)
    result=client.get('/api/memory-patterns').json()
    assert result[0]['status']=='noop' and len(result[0]['evidence_message_ids'])==3
    with store.db:store.db.execute('UPDATE messages SET content=? WHERE id=?',('来源已改写',sources[0]['id']))
    assert client.get('/api/memory-patterns').json()==[]


def test_expiration_retires_only_formed_patterns_without_deleting_sources(env):
    from memoria.memory.formation_store import maintenance
    store,service,_=env
    model(service);cohort(store,service)
    memory=store.memories()[0]
    maintenance(store,'2099-01-01T00:00:00+00:00')
    assert store.retrieval_memories()==[]
    assert store.memory(memory['id']) is not None
    assert store.db.execute('SELECT count(*) FROM messages').fetchone()[0]==3


def test_pattern_cannot_take_over_a_manual_note(env):
    store,service,_=env
    manual=store.add_memory('用户近期在多次对话中关注开发资料','profile',3,'manual')
    model(service);cohort(store,service)
    assert store.memory(manual['id'])['reinforcement']==1
    assert store.memory(manual['id'])['source']=='manual'
    assert store.db.execute("SELECT count(*) FROM memory_pattern_versions WHERE status='active'").fetchone()[0]==0


def test_multi_source_pattern_cannot_be_approved_as_single_claim(env):
    store,service,client=env
    model(service,decision='review');cohort(store,service)
    review=store.memory_reviews()[0]
    response=client.post('/api/memory-reviews/'+review['id']+'/approve',json={'content':'任意改写','kind':'profile','importance':3})
    assert response.status_code==409 and store.memories()==[]


def test_public_sources_never_become_observations(env):
    store,service,_=env
    source=store.add_message(store.create_session()['id'],'user','公开语料中的原话')
    with store.db:
        store.db.execute('INSERT INTO dataset_imports VALUES(?,?,?,?,?)',('public','sha','{}',
            json.dumps({'source_mapping':[{'session_id':source['session_id'],'message_ids':[source['id']]}]}),source['created_at']))
    calls=model(service)
    job=store.enqueue_memory_job(source['id'],source['content'],'')
    asyncio.run(service.memory_worker.process_once())
    assert calls==[] and store.memory_job(job['id'])['status']=='retry'


def test_invalid_json_keeps_usage_instead_of_becoming_empty_success(env):
    store,service,_=env
    async def broken(*args,**kwargs):return ChatResult(content='not-json',raw_message={},usage={'total_tokens':21})
    service.llm.chat=broken
    _,job=send(store,service,'查找开发文章甲')
    assert job['status']=='retry'
    row=store.db.execute('SELECT * FROM memory_formation_calls').fetchone()
    assert row['status']=='invalid_output' and json.loads(row['usage_json'])['total_tokens']==21


@pytest.mark.parametrize('settings',[{'min_sources':1},{'min_sessions':0},{'max_observations':2},
    {'enabled':1},{'retention_days':20,'core_half_life_days':45},{'min_sources':3,'min_sessions':4}])
def test_configuration_rejects_unsafe_thresholds(settings):
    with pytest.raises(ValueError):FormationSettings.from_dict(settings)
