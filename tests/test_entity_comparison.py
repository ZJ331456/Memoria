"""Paired protocol checks; temporary synthetic inputs and local engineering substitutes."""
import asyncio
import copy
from pathlib import Path
import pytest
from memoria.claims import sha
from memoria.experiments.entity_retrieval import MethodEngine,score,load_inputs,seed,run_comparison
from memoria.memory.access import QueryRequest
from memoria.memory.embedding import DocumentEmbedding
from tests.test_memory_kernel import core
from tests.test_entity_retrieval import fixture,remember

ROOT=Path(__file__).resolve().parents[1]


def test_entity_filter_uses_baseline_order_and_only_source_bound_identity(core):
    fixture(core)
    target,_=remember(core,'Memoria的恢复步骤是核对快照。')
    noise,_=remember(core,'Boreal的恢复步骤是清空缓存。')
    class Base:
        async def retrieve(self,query,**kwargs):
            assert kwargs['use_entities'] is False and kwargs['expand_graph'] is False
            return [dict(noise,retrieval={}),dict(target,retrieval={})]
    filtered=asyncio.run(MethodEngine(Base(),core.entities,'entity_filter').retrieve('记忆工坊'))
    assert [r['id'] for r in filtered]==[target['id']]
    assert 'entity_rank' not in filtered[0]['retrieval']
    assert core.entities.dependency_valid(filtered[0]['retrieval']['entity_dependency'])
    unknown=asyncio.run(MethodEngine(Base(),core.entities,'entity_filter').retrieve('没有登记的名字'))
    assert [r['id'] for r in unknown]==[noise['id'],target['id']]


@pytest.mark.parametrize('method',['three_lane_rrf','entity_filter','entity_rrf'])
def test_methods_share_production_candidate_and_evidence_limits(core,method):
    fixture(core);remember(core,'Memoria恢复时核对快照。')
    core.personal_access.engine=MethodEngine(core.memory,core.entities,method)
    request=QueryRequest(query='记忆工坊恢复',mode='rrf',use_entities=method!='three_lane_rrf',
        candidate_limit=8,top_k=4,selector='evidence_coverage',budget_chars=8000,include_experiences=False)
    result=asyncio.run(core.access(request))
    assert result['selection']['budget']['limit']==8000 and result['selection']['cost']<=8000
    assert len(result['candidates'])<=8 and len(result['selection']['payload']['evidence'])<=4
    if method=='entity_rrf':assert result['retrieval']['lanes']['entity']


def test_gold_is_posthoc_and_preserves_zero_denominator_and_losses():
    prediction={'access':{'path':'retrieve','candidates':[{'id':'a'},{'id':'b'}],
        'selection':{'payload':{'evidence':[{'id':'b'}]},'cost':100,'budget':{'limit':200}}}}
    before=copy.deepcopy(prediction)
    first=score(prediction,{'required':['goal'],'wrong_entity':['noise']},{'goal':'a','noise':'b'})
    assert first['required_count']==1 and first['candidate_required']==1 and first['selected_required']==0
    assert first['lost_after_retrieval']==['a'] and first['wrong_entity_selected']==['b']
    second=score(prediction,{'required':[]},{'goal':'a','noise':'b'})
    assert second['required_count']==0 and prediction==before


def test_seed_records_all_sources_and_withdrawal_without_llm_admission(core):
    class Encoder:
        namespace='legacy'
        async def embed_documents(self,texts):return [DocumentEmbedding([1.,0.]) for _ in texts]
    core.embedder=Encoder()
    case=next(c for c in load_inputs(ROOT)['cases'] if c['id']=='source_withdrawn')
    ids,sources=asyncio.run(seed(core,case))
    assert len(sources)==len(case['entities'])*2+len(case['memories'])
    assert core.store.source_revoked(sources['memory:recover'])
    assert core.store.memory(ids['recover']) and core.store.memory(ids['noise'])
    core.llm.chat.assert_not_called()


def test_remote_reader_needs_explicit_authorization_and_ceiling(core):
    configured=copy.deepcopy(core.settings);configured.root=ROOT
    with pytest.raises(ValueError,match='explicit remote permission'):
        asyncio.run(run_comparison(configured,reader_cases=['alias'],max_calls=6))
    core.llm.chat.assert_not_called()


def test_fixed_inputs_and_labels_are_separate_and_complete():
    import json
    suite=load_inputs(ROOT);labels=json.loads((ROOT/'simulation/entity_expectations.json').read_text(encoding='utf-8'))
    assert len(suite['cases'])==8 and set(labels['cases'])=={c['id'] for c in suite['cases']}
    assert all('required' not in c and 'wrong_entity' not in c for c in suite['cases'])
    for case in suite['cases']:
        keys={m['key'] for m in case['memories']}
        assert all(set(labels['cases'][case['id']].get(field,[]))<=keys
            for field in ('required','wrong_entity','revoked','invalid_entity_lane'))
