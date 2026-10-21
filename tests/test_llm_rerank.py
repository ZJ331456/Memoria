"""Frozen candidate advice, complete budget reservations, no live provider calls."""
import asyncio,json
from unittest.mock import AsyncMock
import pytest
from memoria.llm.types import ChatResult
from memoria.memory.rerank import rerank,validate,SYSTEM,seed_policy
from memoria.memory.records import KnowledgeAnswer
from memoria.memory.access import QueryRequest
from memoria.memory.library import SourceInput
from memoria.prompting.assembler import _parse_frame
from tests.test_memory_kernel import core
from tests.test_entity_retrieval import fixture,remember
from tests.test_memory_formation import env as formation_env,model as formation_model,cohort


def decision(rows):
    return {'ranking':[{'id':r['id'],'role':'direct','quotes':[r['quotes'][0]],'reason':'Exact original'} for r in reversed(rows)],'missing_requirements':[]}

def test_complementary_answer_assertion_requires_every_named_fact():
    from memoria.experiments.simulation import check_observation
    assert check_observation({'answer':'快照回滚'},{'answer_all':['快照','哈希']})
    assert not check_observation({'answer':'快照回滚后进行哈希校验'},{'answer_all':['快照','哈希']})

@pytest.mark.parametrize('repair',['valid','invalid','negative'])
def test_pattern_quote_repair_is_bounded_and_never_fills_its_own_evidence(formation_env,repair):
    store,service,_=formation_env;formation_model(service);original=service.llm.chat;reviews=[]
    async def model(messages,**kw):
        result=await original(messages,**kw)
        if '长期记忆的证据审核员' in messages[0]['content']:
            reviews.append(messages);data=json.loads(result.content)
            if len(reviews)==1 or repair=='invalid':data.pop('supporting_quotes',None)
            if len(reviews)==2 and repair=='negative':data.update(decision='review',assessment='Insufficient')
            result.content=json.dumps(data,ensure_ascii=False)
        return result
    service.llm.chat=model;cohort(store,service)
    row=store.memory_reviews('all')[0]
    assert len(reviews)==2 and row['assessment']['model_calls']==2 and len(row['assessment']['attempts'])==2
    assert (row['status']=='approved')==(repair=='valid')
    assert len(service.claims.list())==0

@pytest.mark.parametrize('mutation',['duplicate','unknown','cross_quote','empty','truncated','uncertain_promotion'])
def test_bad_model_rankings_fall_back_without_losing_pool_or_usage(core,mutation):
    rows=[{'id':'a','quotes':['快照恢复方案']},{'id':'b','quotes':['日志恢复方案']}];data=decision(rows)
    if mutation=='duplicate':data['ranking'][1]=data['ranking'][0]
    if mutation=='unknown':data['ranking'][0]['id']='foreign'
    if mutation=='cross_quote':data['ranking'][0]['quotes']=['快照恢复方案']
    if mutation=='empty':data['ranking'][0]['quotes']=[]
    if mutation=='uncertain_promotion':data['ranking'][0].update(role='uncertain',quotes=[])
    core.llm.chat=AsyncMock(return_value=ChatResult(content=json.dumps(data),finish_reason='length' if mutation=='truncated' else 'stop',usage={'total_tokens':17}))
    order,trace=asyncio.run(rerank(core.llm,'恢复方案',rows,[{'role':'user','content':'恢复资料'}]))
    assert order==['a','b'] and trace['status']=='fallback' and trace['calls']==1 and trace['usage']['total_tokens']==17

def test_complete_downstream_budget_is_reserved_before_optional_call(core):
    rows=[{'id':'a','quotes':['快照恢复方案']},{'id':'b','quotes':['日志恢复方案']}]
    order,trace=asyncio.run(rerank(core.llm,'恢复方案',rows,[{'role':'user','content':'x'*165000}]))
    assert order==['a','b'] and trace['calls']==0 and trace['reason']=='complete_pipeline_reservation_unavailable'
    core.llm.chat.assert_not_called()

def test_no_truncation_or_secret_input_and_bounded_pool(core):
    for rows in [[{'id':str(i),'quotes':['资料']} for i in range(9)],
        [{'id':'a','quotes':['x'*16000]},{'id':'b','quotes':['可读资料']}],
        [{'id':'a','quotes':['api_key: engineering-credential-example']},{'id':'b','quotes':['可读资料']}]]:
        _,trace=asyncio.run(rerank(core.llm,'资料',rows,[{'role':'user','content':'资料'}]))
        assert trace['calls']==0 and trace['status']=='skipped'
    core.llm.chat.assert_not_called()

def test_memory_rerank_reselects_under_same_budget_and_updates_replay_hash(core):
    first,_=remember(core,'第一份恢复方案是快照回滚。');second,_=remember(core,'第二份恢复方案是日志重放。')
    async def model(messages,**kw):
        if messages[0]['content']==SYSTEM:
            rows=json.loads(messages[1]['content'])['candidates']
            target=next(r for r in rows if r['id']==second['id']);rest=[r for r in rows if r!=target]
            return ChatResult(content=json.dumps(decision(list(reversed([target]+rest)))))
        _,sections=_parse_frame(messages[1]['content']);packet=json.loads(next(s.content for s in sections if s.name=='Structured State').rsplit('\n',1)[-1])
        assert packet['evidence'][0]['data']['memory_id']==second['id']
        return ChatResult(content=json.dumps({'status':'answer','answer':'日志重放','evidence_ids':packet['allowed_evidence_ids']}))
    core.llm.chat=model
    result=asyncio.run(core.answer(QueryRequest(query='恢复方案',mode='rrf',include_experiences=False,use_llm_rerank=True,selector='topk',top_k=1)))
    assert result['status']=='answer' and result['access']['reranking']['status']=='applied'
    from memoria.observability.memory_trace import fingerprint
    assert result['access']['production_trace']['selection_sha256']==fingerprint(result['access']['selection'])
    from memoria.observability.memory_trace import replay
    replayed=replay(result['access'],core.claims.user_id)
    assert replayed['selections']['6000']['topk']['payload']==result['access']['selection']['payload']

def test_alias_retraction_during_rerank_cannot_be_published(core):
    entity=fixture(core);remember(core,'Memoria的恢复方案是快照。');remember(core,'Memoria的恢复校验使用哈希。')
    async def model(messages,**kw):
        assert messages[0]['content']==SYSTEM
        rows=json.loads(messages[1]['content'])['candidates'];core.entities.revoke(entity['id'],2)
        return ChatResult(content=json.dumps(decision(rows)))
    core.llm.chat=model
    result=asyncio.run(core.answer(QueryRequest(query='记忆工坊恢复',mode='rrf',include_experiences=False,use_entities=True,use_llm_rerank=True)))
    assert result['status']=='clarify' and not result['reader_called'] and result['access']['reason']=='source_changed_during_rerank'
    assert 'entity' in result['access']['production_trace']['changed_stages']

def test_source_reader_rerank_preserves_origins_and_complete_packet_budget(core):
    for content in ['恢复方案使用快照。','恢复校验使用哈希。']:
        core.ingest(SourceInput(kind='document',title='恢复方案',content=content,authorship='external'))
    async def model(messages,**kw):
        if messages[0]['content']==SYSTEM:return ChatResult(content=json.dumps(decision(json.loads(messages[1]['content'])['candidates'])))
        _,sections=_parse_frame(messages[1]['content']);packet=json.loads(next(s.content for s in sections if s.name=='Source Evidence'))
        assert all(e['domain']=='library' for e in packet['evidence'])
        return ChatResult(content=json.dumps({'status':'answer','answer':'按原资料恢复','evidence_ids':[packet['evidence'][0]['citation']]}))
    core.llm.chat=model
    result=asyncio.run(core.answer(KnowledgeAnswer(query='恢复方案',use_llm_rerank=True,budget_chars=5000)))
    assert result['status']=='answer' and result['access']['reranking']['status']=='applied'
    assert result['access']['budget']['used']<=5000 and result['access']['selection']['selected_count']==2


def test_rerank_and_quoted_support_share_actual_call_budget(core):
    remember(core,'第一份恢复方案是快照回滚。');remember(core,'第二份恢复方案是日志重放。');calls=[]
    async def model(messages,**kw):
        system=messages[0]['content'];calls.append(system)
        if system==SYSTEM:return ChatResult(content=json.dumps(decision(json.loads(messages[1]['content'])['candidates'])))
        if system.startswith('Assess'):
            e=json.loads(messages[1]['content'])['evidence'][0]
            return ChatResult(content=json.dumps({'state':'sufficient','requirements':[{'fact':'恢复','status':'supported','sources':[{'id':e['id'],'quote':e['source_texts'][0]}]}],'missing_queries':[],'reason':'Direct'}))
        if system.startswith('Verify'):
            p=json.loads(messages[1]['content']);e=p['evidence'][0]
            return ChatResult(content=json.dumps({'verdict':'supported','claims':[{'statement':p['proposed_answer'],'relation':'supported','basis':'explicit','sources':[{'id':e['id'],'quote':e['source_texts'][0]}],'reason':'Direct'}],'missing_queries':[],'reason':'Direct'}))
        _,sections=_parse_frame(messages[1]['content']);p=json.loads(next(s.content for s in sections if s.name=='Structured State').rsplit('\n',1)[-1])
        return ChatResult(content=json.dumps({'status':'answer','answer':'恢复方案见原文','evidence_ids':[p['allowed_evidence_ids'][0]]}))
    core.llm.chat=model
    result=asyncio.run(core.answer(QueryRequest(query='恢复方案',mode='rrf',include_experiences=False,
        use_llm_rerank=True,use_answer_risk=True,selector='topk',top_k=1)))
    assert result['status']=='answer' and len(calls)==4 and result['risk_checks']['policy']['calls']==4


def test_benchmark_inputs_do_not_use_gold_and_freeze_artifacts(core,tmp_path,monkeypatch):
    from memoria.experiments import rerank_comparison as comparison
    from memoria.experiments.records import digest_file
    path=tmp_path/'official.json'
    record={'sample_id':'conv-test','qa':[{'question':'Which recovery protocol?','answer':'logs','category':1,'evidence':['D1:1']} for _ in range(4)],
        'conversation':{'session_1_date_time':'May 7 2023','session_1':[{'dia_id':'D1:1','speaker':'A','text':'Recovery uses logs.'},{'dia_id':'D1:2','speaker':'B','text':'Recovery uses snapshots.'}]}}
    path.write_text(json.dumps([record]),encoding='utf-8');monkeypatch.setattr(comparison,'SOURCE_SHA256',digest_file(path))
    frozen=comparison.inputs(path);assert all(not {'answer','evidence','category'} & set(q) for q in frozen['questions'])
    for q in record['qa']:q.update(answer='changed gold',category=5,evidence=['absent'])
    path.write_text(json.dumps([record]),encoding='utf-8');monkeypatch.setattr(comparison,'SOURCE_SHA256',digest_file(path))
    changed=comparison.inputs(path);assert changed['questions']==frozen['questions'] and changed['history']==frozen['history']
    async def model(self,messages,**kw):
        if messages[0]['content']==SYSTEM:return ChatResult(content=json.dumps(decision(json.loads(messages[1]['content'])['candidates'])),usage={'total_tokens':10})
        _,sections=_parse_frame(messages[1]['content']);p=json.loads(next(s.content for s in sections if s.name=='Source Evidence'))
        return ChatResult(content=json.dumps({'status':'answer','answer':'logs','evidence_ids':[p['evidence'][0]['citation']]}),usage={'total_tokens':10})
    monkeypatch.setattr(comparison.LLMClient,'chat',model)
    settings=core.settings;settings.root=tmp_path
    result=asyncio.run(comparison.run(settings,path));assert result['execution_failures']==0
    directory=__import__('pathlib').Path(result['directory']);manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    assert all(digest_file(directory/k)==v for k,v in manifest['artifacts'].items())
    assert all(s['questions']==4 and s['mean_token_f1']==0 for s in result['summary'].values())
