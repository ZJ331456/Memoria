"""Artificial intent fixtures; never imported as personal data or benchmark gold."""
import asyncio

import pytest

from memoria.api.app import _create_tenant_app
from memoria.llm import ChatResult
from memoria.memory.access import infer_query
from memoria.memory.planner import RetrievalPlan
from memoria.memory.reader import SAFE_UNKNOWN


@pytest.mark.parametrize('query',[
    '我现在住在哪里？', '我去年住在哪里？',
    '上个月的时候，你认为我住在哪里？', '我什么时候从南京搬到上海的？',
    '我喜欢用什么语言回答？', '我的回答语言偏好？', '我用什么语言回答？',
    '面试模拟时我希望用什么语言回答？', '我平时吃什么？', '我有哪些忌口？',
    '我的预算是多少？', '我做什么工作？', '我以前在哪所大学？',
    '我的学习目标是什么？', '我的工作习惯有哪些？',
    '我朋友去年住哪里？',
])
def test_fact_recall_keeps_strict_memory_route(query):
    assert infer_query(query) is not None


@pytest.mark.parametrize('query',[
    '我没有吃什么特别的，为什么花盆里的植物还是枯了？',
    '我没吃什么特别的，今天不舒服。',
    '我现在住在顶层，为什么房间这么热？',
    '我应该吃什么才方便准备午饭？', '我平时吃什么比较方便？',
    '我的预算够不够，能买什么电脑？', '我的预算不多，推荐什么电脑？',
    '我该不该换工作，职业规划怎么安排？',
    '我的学校附近适合办什么活动？',
    '帮我总结我的学习目标', '我想用英文，如何练习？',
    '我在考虑职业变化，怎么回事？',
])
def test_advice_causes_and_negated_whatever_use_normal_dialogue(query):
    assert infer_query(query) is None


@pytest.mark.parametrize('personal_memory',[False,True])
def test_runtime_missing_claims_only_blocks_actual_fact_recall(tmp_path,personal_memory):
    config=tmp_path/'runtime.toml'
    config.write_text('[storage]\ndatabase="'+(tmp_path/'runtime.db').as_posix()+
        '"\n[agent.mcp]\nenabled=false\n[memory.markdown]\nenabled=false\n')
    app=_create_tenant_app(config);store=app.state.store;service=app.state.service
    session=store.create_session('Isolated engineering dialogue')['id']
    calls=[]
    async def plan(*args,**kwargs):
        return RetrievalPlan(False,'engineering',reason='engineering_fixture')
    async def reader(*args,**kwargs):
        calls.append(True)
        return ChatResult(content='工程通用回答',raw_message={'content':'工程通用回答'})
    service.runtime.retrieval_planner.plan=plan
    service.runtime.llm.chat=reader
    try:
        query='我的预算是多少？' if personal_memory else '我现在住在顶层，为什么房间这么热？'
        answer,_,trace=asyncio.run(service.runtime.run(session,query,skip_memory_enqueue=True))
        assert trace['metadata']['query_route']==('personal_memory' if personal_memory else 'dialogue')
        if personal_memory:
            assert calls==[] and answer['content']==SAFE_UNKNOWN
            assert trace['metadata']['citation_validation']['reason']=='access_requires_clarification'
        else:
            assert calls==[True] and answer['content']=='工程通用回答'
            assert 'claim_access' not in trace['metadata']
    finally:
        store.close()
