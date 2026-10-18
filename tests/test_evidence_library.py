"""Engineering-only documents/tasks; no invented personal history or research scores."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from memoria.api.app import _create_tenant_app
from memoria.api import create_app
from memoria.llm.types import ChatResult
from memoria.memory.library import LibraryError, SourceInput, RevisionInput, FeedbackInput
from memoria.observability.context import RequestContext


@pytest.fixture
def env(tmp_path):
    config=tmp_path/'config.toml'
    config.write_text('[storage]\ndatabase="'+(tmp_path/'test.db').as_posix()+'"\n'
        '[memory.markdown]\nenabled=false\n[llm.main]\nmodel="engineering-only"\n'
        'base_url="https://model.invalid/v1"\napi_key="engineering-only"\n',encoding='utf-8')
    app=_create_tenant_app(config)
    yield app.state.store,app.state.service,TestClient(app)
    app.state.store.close()


def document(library,**fields):
    return library.create(SourceInput(**({'kind':'document','title':'工程资料','content':'部署需要先完成配置校验，再运行服务。','authorship':'external'}|fields)))


def model(service, *, assessment='Supported', mutate=None):
    calls=[]
    async def chat(messages,**kwargs):
        calls.append(messages)
        data=json.loads(messages[1]['content'])
        if '知识与任务经验整理器' in messages[0]['content']:
            output={'content':'据本次资料，运行服务前需要完成配置校验。','quote':data['source']['content'],
                'feedback_quote':data['feedback']['content'] if data['feedback'] else '',
                'applicability':'仅限所给资料或本次任务，尚未验证通用性'}
        else:
            output={'assessment':assessment,'conditions_preserved':True,'task_bound':True,'sensitive':False}
        if mutate:mutate(len(calls),output)
        return ChatResult(content=json.dumps(output,ensure_ascii=False),raw_message={},usage={'total_tokens':19})
    service.llm.chat=chat
    return calls


def test_native_sources_are_not_conversations_or_claims(env):
    store,service,client=env
    source=document(service.library)
    assert store.sessions()==[] and store.memories()==[] and store.memory_reviews('all')==[]
    assert store.db.execute('SELECT count(*) FROM messages').fetchone()[0]==0
    assert store.db.execute('SELECT count(*) FROM claim_roots').fetchone()[0]==0
    assert client.get('/api/library/sources').json()[0]['id']==source['id']
    assert client.post('/api/library/search',json={'query':'配置校验'}).json()[0]['source_id']==source['id']
    assert source['authorship']=='external'


@pytest.mark.parametrize('text,query',[('请先完成配置校验。','配置校验'),('Preserve English spaces during token matching.','English spaces')])
def test_lexical_chunks_preserve_exact_spans(env,text,query):
    _,service,_=env
    item=document(service.library,content='起始资料\n'+'前文。'*270+text)
    hit=service.library.search(query)[0]
    original=item['revision']['content']
    assert original[hit['start']:hit['end']]==hit['quote']
    assert query.split()[0] in hit['quote']
    assert service.library.dependency_valid(hit['dependency'])


def test_working_scope_requires_real_session_and_is_not_global(env):
    store,service,_=env
    with pytest.raises(LibraryError):document(service.library,scope='working')
    with pytest.raises(LibraryError):document(service.library,scope='working',session_id='not-a-session')
    a,b=store.create_session()['id'],store.create_session()['id']
    document(service.library,scope='working',session_id=a)
    assert service.library.search('配置校验')==[]
    assert service.library.search('配置校验',session_id=b)==[]
    assert len(service.library.search('配置校验',session_id=a))==1
    with pytest.raises(LibraryError):document(service.library,session_id=a)


def test_revision_is_immutable_and_stale_head_cannot_publish(env):
    store,service,_=env
    item=document(service.library);old=item['revision']['id']
    dependency=service.library.search('配置校验')[0]['dependency']
    new=service.library.revise(item['id'],RevisionInput(expected_revision_id=old,title='修订资料',content='新版改为先完成来源核对。'))
    assert new['revision']['previous_id']==old and len(new['versions'])==2
    assert not service.library.dependency_valid(dependency)
    assert service.library.search('配置校验')==[]
    assert service.library.search('来源核对')
    assert service.library.version(item['id'],old)['content']==item['revision']['content']
    assert not service.library.version(item['id'],old)['is_current']
    with pytest.raises(LibraryError):service.library.revise(item['id'],RevisionInput(expected_revision_id=old,title='旧更新',content='不得覆盖'))
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        with store._memory_transaction():store.db.execute('UPDATE library_revisions SET content=? WHERE id=?',('伪造',old))


def test_retraction_invalidates_knowledge_and_runtime_dependency(env):
    _,service,_=env
    source=document(service.library)
    dep=service.library.search('配置校验')[0]['dependency']
    assert service.runtime._memory_dependency_valid(dep)
    service.library.retract(source['id'])
    assert not service.runtime._memory_dependency_valid(dep)
    assert service.library.search('配置校验')==[]


def test_distill_has_two_calls_and_reuses_the_same_input(env):
    store,service,_=env
    calls=model(service);source=document(service.library)
    card=asyncio.run(service.library.distill(source['id']))
    assert card['status']=='approved' and len(calls)==2
    assert asyncio.run(service.library.distill(source['id']))['id']==card['id']
    assert len(calls)==2
    assert sum(json.loads(r[0])['total_tokens'] for r in store.db.execute('SELECT usage_json FROM library_calls'))==38
    assert service.library.search('配置校验')[0]['knowledge_cards'][0]['quote']==source['revision']['content']
    assert store.memories()==[]


def test_model_may_reject_well_formed_but_unsupported_knowledge(env):
    _,service,_=env
    model(service,assessment='Insufficient');source=document(service.library)
    assert asyncio.run(service.library.distill(source['id']))['status']=='needs_assistance'
    assert service.library.search('配置校验')[0]['knowledge_cards']==[]


def test_concurrent_duplicate_requests_are_coalesced(env):
    _,service,_=env
    calls=model(service);source=document(service.library)
    async def concurrent():
        return await asyncio.gather(service.library.distill(source['id']),service.library.distill(source['id']))
    a,b=asyncio.run(concurrent())
    assert a['id']==b['id'] and len(calls)==2


def test_noop_is_cached_without_publishing_or_repeating_cost(env):
    store,service,_=env
    calls=[]
    async def chat(*args,**kwargs):
        calls.append(1)
        return ChatResult(content='null',raw_message={},usage={'total_tokens':9})
    service.llm.chat=chat;source=document(service.library)
    assert asyncio.run(service.library.distill(source['id']))=={'status':'noop'}
    assert asyncio.run(service.library.distill(source['id']))=={'status':'noop'}
    assert len(calls)==1 and store.db.execute('SELECT count(*) FROM library_cards').fetchone()[0]==0


def test_provider_failure_keeps_unknown_usage_and_never_publishes(env):
    store,service,_=env
    async def unavailable(*args,**kwargs):raise TimeoutError('engineering provider failure')
    service.llm.chat=unavailable;source=document(service.library)
    with pytest.raises(LibraryError,match='上游模型暂不可用'):asyncio.run(service.library.distill(source['id']))
    row=store.db.execute('SELECT * FROM library_calls').fetchone()
    assert row['status']=='provider_failed' and row['usage_json'] is None
    assert store.db.execute('SELECT count(*) FROM library_cards').fetchone()[0]==0


def test_budget_refuses_generation_instead_of_clipping_evidence(env):
    from memoria.llm.types import ContextLengthError
    _,service,_=env
    calls=model(service);source=document(service.library)
    service.settings.reader_input_bytes=256
    with pytest.raises((LibraryError,ContextLengthError)):asyncio.run(service.library.distill(source['id']))
    assert calls==[]


def test_length_failure_cannot_publish_even_if_json_looks_complete(env):
    store,service,client=env
    async def truncated(*args,**kwargs):
        return ChatResult(content='null',raw_message={},usage={'total_tokens':12},finish_reason='length')
    service.llm.chat=truncated;source=document(service.library)
    response=client.post('/api/library/sources/'+source['id']+'/distill',json={})
    assert response.status_code==502
    row=store.db.execute('SELECT status,usage_json FROM library_calls').fetchone()
    assert row['status']=='incomplete_output' and json.loads(row['usage_json'])['total_tokens']==12
    assert store.db.execute('SELECT count(*) FROM library_decisions').fetchone()[0]==0


def test_fabricated_quote_stops_before_second_model(env):
    _,service,_=env
    calls=model(service,mutate=lambda n,out:out.update(quote='并不存在的原话') if n==1 else None)
    source=document(service.library)
    with pytest.raises(LibraryError):asyncio.run(service.library.distill(source['id']))
    assert len(calls)==1


def test_task_feedback_is_required_and_corrections_invalidate_old_lesson(env):
    _,service,_=env
    calls=model(service);source=document(service.library,kind='task_result',authorship='self')
    with pytest.raises(LibraryError):asyncio.run(service.library.distill(source['id']))
    assert calls==[]
    body=FeedbackInput(expected_revision_id=source['revision']['id'],outcome='failure',content='配置检查未通过，未满足启动验收。')
    service.library.feedback(source['id'],body)
    card=asyncio.run(service.library.distill(source['id']))
    assert card['status']=='approved'
    hit=service.library.search('配置校验')[0]
    assert hit['knowledge_cards'][0]['reported_outcome']=='failure'
    service.library.feedback(source['id'],FeedbackInput(**(body.model_dump()|{'outcome':'unknown','content':'此前判断有误，验收结果待确认。'})))
    assert not service.library.dependency_valid(hit['dependency'])
    assert service.library.search('配置校验')[0]['knowledge_cards']==[]
    assert not service.library.detail(source['id'])['cards'][0]['available']


@pytest.mark.parametrize('change',['revision','feedback','retract'])
def test_model_wait_cannot_publish_after_source_or_feedback_changes(env,change):
    _,service,_=env
    source=document(service.library,kind='task_result')
    rid=source['revision']['id']
    service.library.feedback(source['id'],FeedbackInput(expected_revision_id=rid,outcome='unknown',content='结果待核对。'))
    def mutate(n,out):
        if n!=2:return
        if change=='revision':service.library.revise(source['id'],RevisionInput(expected_revision_id=rid,title='新版',content='更新的真实来源'))
        elif change=='feedback':service.library.feedback(source['id'],FeedbackInput(expected_revision_id=rid,outcome='failure',content='新的失败反馈'))
        else:service.library.retract(source['id'])
    model(service,mutate=mutate)
    with pytest.raises(LibraryError):asyncio.run(service.library.distill(source['id']))
    assert service.store.db.execute('SELECT count(*) FROM library_cards').fetchone()[0]==0


def test_tool_uses_server_session_and_shared_read_budget(env):
    from memoria.memory.budget import TurnMemoryBudget
    store,service,_=env
    sid=store.create_session()['id'];document(service.library,scope='working',session_id=sid)
    with RequestContext(session_id=sid):
        result=asyncio.run(service.runtime.tools.execute('search_library',{'query':'配置校验'}))
    assert result.ok and result.memory_dependencies
    assert 'library:' in result.content
    bad=asyncio.run(service.runtime.tools.execute('search_library',{'query':'配置校验','session_id':sid}))
    assert not bad.ok
    budget=TurnMemoryBudget(1,1000)
    assert budget.allow_call('search_library') and not budget.allow_call('search_library')


@pytest.mark.parametrize('content',['api_key=engineering_secret_example_123456789',' '])
def test_credentials_and_empty_sources_do_not_reach_model(env,content):
    _,service,_=env
    calls=model(service)
    with pytest.raises(LibraryError):
        source=document(service.library,content=content)
        asyncio.run(service.library.distill(source['id']))
    assert calls==[]


def test_two_accounts_never_share_library_sources_or_feedback(tmp_path):
    config=tmp_path/'config.toml'
    config.write_text('[storage]\ndatabase="'+(tmp_path/'data/memoria.db').as_posix()+'"\n[memory.markdown]\nenabled=false\n',encoding='utf-8')
    app=create_app(config)
    with TestClient(app) as client:
        def login(name):
            assert client.post('/api/auth/register',json={'username':name,'password':'Engineering-only-773!'}).status_code==201
            token=client.post('/api/auth/login',json={'username':name,'password':'Engineering-only-773!'}).json()['access_token']
            return {'Authorization':'Bearer '+token}
        a,b=login('library_a'),login('library_b')
        source=client.post('/api/library/sources',headers=a,json={'kind':'note','title':'账户A资料','content':'隔离资料内容'}).json()
        assert client.get('/api/library/sources',headers=b).json()==[]
        for endpoint in ('','/distill','/retract'):
            response=client.get('/api/library/sources/'+source['id'],headers=b) if not endpoint else client.post('/api/library/sources/'+source['id']+endpoint,headers=b,json={})
            assert response.status_code==404
        assert client.post('/api/library/search',headers=b,json={'query':'隔离资料'}).json()==[]
        client.cookies.clear()
        assert client.get('/api/library/sources').status_code==401
