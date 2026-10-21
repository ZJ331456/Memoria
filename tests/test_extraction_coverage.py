"""Bounded extraction, failure isolation and source-bound diagnostics, not semantic F1."""
import asyncio
import copy
import json
import pytest
from memoria.memory import extraction
from memoria.memory.formation import Observation
from memoria.memory.library import SourceInput,LibraryError
from memoria.memory.source_formation import OBSERVE,VERIFY,FormRequest
from tests.test_memory_formation import env,model,observer,send


def test_dynamic_allowance_is_bounded_and_output_budget_grows():
    assert extraction.allowance('一次简单询问。')==4
    assert extraction.allowance('甲。乙。丙。丁。戊。己。')==6
    assert extraction.allowance('一。'*200)==12
    assert extraction.output_tokens(1800,12)>extraction.output_tokens(1800,4)
    with pytest.raises(ValueError):extraction.envelope({'observations':[{}]*33},'observations')


def test_bad_item_duplicate_and_quota_do_not_hide_valid_items():
    text='甲；乙；丙；丁；戊；己'
    rows=[observer(c) for c in text.split('；')]
    rows.insert(1,{'quote':'invalid schema'});rows.append(rows[0])
    items,report=extraction.proposals({'observations':rows},Observation,text,lambda o:None)
    assert len(items)==6 and report['proposed']==8 and len(report['rejected'])==2
    assert report['accepted_indices']==[0,2,3,4,5,6]


def test_coverage_keeps_global_coordinates_and_unknown_semantics():
    report=extraction.coverage('甲；乙；丙',['甲','丙'],offset=20)
    assert report['uncovered']==[{'start':22,'end':23,'quote':'乙','reason':'not_fully_covered_by_valid_observation_quote'}]
    assert report['semantic_completeness_verified'] is False
    assert extraction.coverage('同文；同文',['同文'])['uncovered']


def test_request_subtasks_share_only_literal_object_not_different_entities():
    for obj in ('Aster','Boreal','数据库索引'):
        for task in ('入门','性能分析','维护'):
            item=Observation.model_validate(observer('请查找'+obj+'的'+task+'资料',topic=obj+task))
            assert extraction.request_topic(item)==obj.casefold()
            item.asserted=True
            assert extraction.request_topic(item)==obj+task
    item=Observation.model_validate(observer('我在做数据库索引优化',topic='索引优化'))
    assert extraction.request_topic(item)=='索引优化'


def test_conversation_partial_survives_cache_and_retraction_hides_diagnostics(env):
    store,service,client=env;text='甲；乙；丙；丁；戊；己；庚'
    def mutate(stage,output,data):
        if stage=='observe':
            assert data['extraction_budget']==7
            output['observations']=[observer(c) for c in text.split('；')[:6]]
            output['observations']+=[observer('不存在的原话'),copy.deepcopy(output['observations'][0])]
    calls=model(service,mutate=mutate);source,job=send(store,service,text)
    assert job['status']=='completed' and job['extracted_count']==0
    assert store.db.execute('SELECT COUNT(*) FROM memory_observations').fetchone()[0]==6
    packet=client.get('/api/memory-extraction-diagnostics').json()[0]
    assert 'raw_json' not in packet and packet['diagnostics']['accepted']==6
    assert len(packet['diagnostics']['rejected'])==2 and packet['diagnostics']['coverage']['uncovered'][0]['quote']=='庚'
    cached=asyncio.run(service.formation.extract({**job,'user_text':text}))
    assert len(cached['observations'])==6 and cached['diagnostics']['accepted']==6 and calls==['observe']
    store.undo_memory_sources([source['id']])
    assert client.get('/api/memory-extraction-diagnostics').json()==[]
    assert store.db.execute('SELECT raw_json FROM memory_extraction_diagnostics').fetchone()[0]


def test_library_bad_item_and_missing_review_are_isolated(env):
    store,service,_=env;library=service.library
    text='Aster备份。Boreal审查。Cedar回归。Dahlia监控。Elm文档。Fjord校验。'
    async def chat(messages,**kwargs):
        from memoria.llm.types import ChatResult
        data=json.loads(messages[1]['content'])
        if messages[0]['content']==OBSERVE:
            assert data['extraction_budget']==6
            rows=[{'category':'knowledge','topic':word,'subject':word.split()[0], 'conditions':'',
                'polarity':'positive','epistemic':'asserted','content':word,'quote':word,'subject_quote':'',
                'feedback_quote':'','applicability':'仅限原资料'} for word in text.split('。') if word]
            # Keep literal subject names, then make one malformed proposal among six valid.
            for row,name in zip(rows,['Aster','Boreal','Cedar','Dahlia','Elm','Fjord']):row['subject']=name
            rows.insert(1,{'quote':'invalid'})
            output={'observations':rows}
        elif messages[0]['content']==VERIFY:
            assert len(data['candidates'])==6
            output={'verdicts':[{'id':c['id'],'assessment':'Supported','attribution_preserved':True,
                'conditions_preserved':True,'task_bound':True,'sensitive':False} for c in data['candidates'][1:]]}
        else:raise AssertionError('No consolidation before source threshold')
        return ChatResult(content=json.dumps(output,ensure_ascii=False),usage={'total_tokens':21})
    service.llm.chat=chat
    source=library.create(SourceInput(kind='document',title='fixture',content=text,authorship='external'))
    result=asyncio.run(library.formation.form(source['id'],FormRequest()))
    assert len(result['observations'])==6
    assert sum(o['status']=='approved' for o in result['observations'])==5
    assert result['observations'][0]['status']=='needs_assistance'
    assert result['diagnostics']['verification']['missing_or_invalid_ids']==['o0']
    card=library.detail(source['id'])['cards'][0]
    assert all(o['lane']=='source_knowledge' for o in result['observations'])
    assert store.db.execute('SELECT COUNT(*) FROM claim_roots').fetchone()[0]==0


def test_diagnostic_raw_is_not_public_and_delete_cleans_private_output(env):
    store,service,client=env;model(service)
    source,job=send(store,service,'查找开发文章甲')
    assert client.get('/api/memory-extraction-diagnostics?session_id=other').status_code==404
    with store.memory_transaction():store.db.execute('DELETE FROM messages WHERE id=?',(source['id'],))
    assert store.db.execute('SELECT count(*) FROM memory_extraction_diagnostics').fetchone()[0]==0


def test_diagnostics_are_authenticated_and_owner_scoped(tmp_path,monkeypatch):
    from unittest.mock import AsyncMock
    from fastapi.testclient import TestClient
    from memoria.api import create_app
    from memoria.service import AgentService
    from tests.test_memory_kernel import config_at
    monkeypatch.setattr(AgentService,'start_integrations',AsyncMock())
    app=create_app(config_at(tmp_path))
    with TestClient(app) as client:
        def signup(name):
            body={'username':name,'password':'Engineering-only-passphrase-2026'}
            assert client.post('/api/auth/register',json=body).status_code==201
            login=client.post('/api/auth/login',json=body).json()
            return login['user']['id'],{'Authorization':'Bearer '+login['access_token']}
        owner,a=signup('coverage_a');_,b=signup('coverage_b')
        # Loading the owner runtime precedes direct, isolated engineering setup.
        assert client.get('/api/memory-extraction-diagnostics',headers=a).status_code==200
        service=app.state.tenants.apps[owner].state.service
        model(service);send(service.store,service,'查找开发文章甲')
        assert len(client.get('/api/memory-extraction-diagnostics',headers=a).json())==1
        assert client.get('/api/memory-extraction-diagnostics',headers=b).json()==[]
        client.cookies.clear()
        assert client.get('/api/memory-extraction-diagnostics').status_code==401


def test_invalid_library_verdict_identifier_preserves_other_reviewed_items(env):
    store,service,_=env
    from tests.test_source_formation import model as source_model
    def alter(stage,data,output,index):
        if stage==OBSERVE:
            base=output['observations'][0]
            output['observations']=[base|{'quote':'Memoria需要备份。','content':'Memoria需要备份。'},
                base|{'quote':'Memoria需要校验。','content':'Memoria需要校验。'}]
        if stage==VERIFY:output['verdicts'][0]['id']=[]
    source_model(service,alter=alter)
    source=service.library.create(SourceInput(kind='document',title='fixture',content='Memoria需要备份。Memoria需要校验。',authorship='external'))
    result=asyncio.run(service.library.formation.form(source['id'],FormRequest()))
    assert [o['status'] for o in result['observations']]==['needs_assistance','approved']
    assert result['diagnostics']['verification']['missing_or_invalid_ids']==['o0']
