"""Conservative relation-veto engineering cases, not extraction or semantic quality."""
import asyncio
import pytest
from memoria.memory import MemoryEngine
from memoria.memory.ordinary_update_policy import binding,compatibility,decision
from memoria.store import Store,MemoryVersionConflict


@pytest.mark.parametrize('new,old',[
    ('Alice prefers concise replies.','Bob prefers concise replies.'),
    ('我面试模拟时希望用英文回答','我平时希望用中文回答'),
    ('我暂时面试模拟时希望用英文回答','我面试模拟时希望用英文回答'),
    ('我去年住在南京','我现在住在南京'),
    ('我妈妈喜欢咖啡','我喜欢咖啡'),
    ('我可能喜欢咖啡','我喜欢咖啡'),
    ('我准备住在上海','我住在上海'),
    ('我本次会话希望英文回答','我希望英文回答'),
    ('我不是不喜欢咖啡','我喜欢咖啡'),
    ('用户小王喜欢咖啡','用户喜欢咖啡'),
])
def test_mismatched_or_unknown_bindings_veto_destructive_relation(new,old):
    assert not compatibility(new,old)[0]
    assert not decision(new,old,'supersede','reviewed_conversation')['allowed']


def test_multivalue_and_opposite_polarity_are_not_model_reinforcement():
    assert not decision('我喜欢绿茶','我喜欢红茶','supersede','reviewed_conversation')['allowed']
    assert not decision('我不喜欢咖啡','我喜欢咖啡','reinforce','reviewed_conversation')['allowed']
    assert decision('用户目标是每周跑步 20 公里','用户目标是每周跑步 10 公里','supersede','manual')['allowed']
    assert not decision('我住在上海','我住在南京','supersede','conversation')['allowed']


def test_compatible_targets_filter_before_top3_and_unknown_entities_never_reach_decider(tmp_path):
    store=Store(tmp_path/'updates.db','json')
    try:
        for name in ('Alice','Bob','Carol','David'):
            store.add_memory(name+' prefers concise replies.','preference')
        calls=[]
        async def malicious(content,kind,candidates):
            calls.append(candidates);return {'action':'supersede','target_id':candidates[0]['id']}
        result=asyncio.run(MemoryEngine(store,decider=malicious).remember('Eve prefers concise replies.','preference',3,'manual'))
        assert result.action=='created' and not calls
        assert result.write_diagnostics['rejected_candidate_count']>=4
        assert len(store.memories())==5
    finally:store.close()


def test_model_cannot_force_multivalue_supersede_or_opposite_reinforce(tmp_path):
    store=Store(tmp_path/'updates.db','json')
    try:
        first=store.add_memory('我喜欢咖啡','preference')
        async def malicious(content,kind,candidates):return {'action':'reinforce','target_id':first['id']}
        result=asyncio.run(MemoryEngine(store,decider=malicious).remember('我不喜欢咖啡','preference',3,'reviewed_conversation'))
        assert result.action=='created' and store.memory(first['id'])['reinforcement']==1
        assert not result.write_diagnostics['action_gate']['allowed']
    finally:store.close()


def test_context_compatible_lower_similarity_target_is_not_lost_to_other_scenes(tmp_path):
    store=Store(tmp_path/'updates.db','json')
    try:
        for text in ('我面试模拟时希望英文回答','我学习时希望英文回答','我工作时希望英文回答'):
            store.add_memory(text,'preference')
        target=store.add_memory('我平时希望中文回答','preference')
        seen=[]
        async def choose(content,kind,candidates):
            seen.extend(candidates);return {'action':'supersede','target_id':target['id']}
        result=asyncio.run(MemoryEngine(store,decider=choose).remember('我平时希望英文回答','preference',3,'reviewed_conversation'))
        assert [c['id'] for c in seen]==[target['id']]
        assert result.action=='superseded' and len(store.memories())==4
    finally:store.close()


def test_target_change_during_decider_await_cannot_overwrite_new_version(tmp_path):
    store=Store(tmp_path/'updates.db','json')
    try:
        target=store.add_memory('我喜欢咖啡','preference')
        async def race(content,kind,candidates):
            with store.memory_transaction():store.db.execute("UPDATE memories SET content='我喜欢红茶' WHERE id=?",(target['id'],))
            return {'action':'supersede','target_id':target['id']}
        with pytest.raises(MemoryVersionConflict):
            asyncio.run(MemoryEngine(store,decider=race).remember('我不喜欢咖啡','preference',3,'reviewed_conversation'))
        assert store.memory(target['id'])['content']=='我喜欢红茶' and len(store.memories())==1
    finally:store.close()


def test_explicit_multi_value_replacement_requires_unique_eligible_target():
    new,old='我现在改为喜欢红茶','我喜欢咖啡'
    assert decision(new,old,'supersede','reviewed_conversation',candidate_count=1)['allowed']
    assert not decision(new,old,'supersede','reviewed_conversation',candidate_count=2)['allowed']
