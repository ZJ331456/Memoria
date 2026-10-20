"""Opt-in entity lane and fresh citations; temporary sources, no live API calls."""
import asyncio,json
import pytest
from memoria.claims import sha
from memoria.memory.library import SourceInput,RevisionInput
from memoria.memory.records import MemorySearch,KnowledgeAnswer
from memoria.memory.access import QueryRequest
from memoria.memory.entities import AliasInput
from memoria.llm.types import ChatResult
from tests.test_memory_kernel import core
from tests.test_entities import register,message,anchor


def fixture(core):
    entity,_=register(core)
    s=message(core,'Memoria又名记忆工坊。')
    core.entities.alias(entity['id'],AliasInput(expected_revision=1,name='记忆工坊',evidence=anchor(s)))
    return entity


def remember(core,text):
    source=message(core,text)
    result=asyncio.run(core.memory.remember(text,'fact',3,'engineering',source['id'],require_source=True))
    return result.memory,source


def retrieve(core,query,**kwargs):
    diag={};rows=asyncio.run(core.memory.retrieve(query,expand_graph=False,diagnostics=diag,**kwargs));return rows,diag


def test_alias_lane_is_off_by_default_and_one_rrf_vote_per_memory(core):
    entity=fixture(core);memory,_=remember(core,'Memoria使用快照进行故障恢复。')
    baseline,diag=retrieve(core,'记忆工坊')
    assert baseline==[] and 'entity' not in diag['lanes']
    rows,diag=retrieve(core,'记忆工坊',use_entities=True)
    assert [r['id'] for r in rows]==[memory['id']]
    assert diag['lanes']['entity']==[memory['id']] and rows[0]['retrieval']['entity_rank']==1
    assert rows[0]['retrieval']['score']==round(.8/61,6)
    repeated,diag=retrieve(core,'记忆工坊 记忆工坊',use_entities=True)
    assert repeated[0]['retrieval']['score']==rows[0]['retrieval']['score']
    record=core.readers.memory(rows[0]);assert core.dependency_valid(record.dependency)
    core.entities.revoke(entity['id'],2)
    assert not core.dependency_valid(record.dependency)
    assert retrieve(core,'记忆工坊',use_entities=True)[0]==[]


def test_same_name_blocks_all_lanes_and_explicit_type_can_disambiguate(core):
    entity=fixture(core);memory,_=remember(core,'Memoria使用快照恢复。')
    register(core,kind='person')
    rows,diag=retrieve(core,'Memoria',use_entities=True)
    assert rows==[] and diag['entity_blocked']=='ambiguous_identity'
    assert retrieve(core,'Memoria',use_entities=True,entity_type='project')[0]
    result=asyncio.run(core.answer(KnowledgeAnswer(query='Memoria',use_entities=True)))
    assert not result['reader_called'] and result['status']=='abstain'


def test_source_retraction_and_revision_drop_entity_candidates(core):
    fixture(core);memory,source=remember(core,'Memoria使用快照恢复。')
    assert retrieve(core,'记忆工坊',use_entities=True)[0]
    core.store.undo_memory_sources([source['id']])
    assert retrieve(core,'记忆工坊',use_entities=True)[0]==[]


def test_library_alias_retrieval_uses_live_raw_source_and_scope(core):
    entity=fixture(core);sid=core.store.create_session()['id']
    item=core.ingest(SourceInput(kind='document',title='记录',content='Memoria采用快照恢复。',authorship='external',scope='working',session_id=sid))
    args=dict(query='记忆工坊',domains=['library'],memory_mode='keyword',budget_chars=4000)
    assert asyncio.run(core.search(MemorySearch(**args)))['evidence']==[]
    assert asyncio.run(core.search(MemorySearch(**args,use_entities=True)))['evidence']==[]
    result=asyncio.run(core.search(MemorySearch(**args,use_entities=True,session_id=sid)))
    assert len(result['evidence'])==1 and result['evidence'][0]['provenance']['authorship']=='external'
    dep=result['dependencies'][0];assert core.dependency_valid(dep,session_id=sid)
    core.library.revise(item['id'],RevisionInput(expected_revision_id=item['revision']['id'],title='new',content='新版没有提及该项目。'))
    assert not core.dependency_valid(dep,session_id=sid)
    assert not asyncio.run(core.search(MemorySearch(**args,use_entities=True,session_id=sid)))['evidence']


def test_entity_change_while_embedding_awaits_drops_result(core):
    entity=fixture(core);memory,_=remember(core,'Memoria使用快照恢复。')
    class Encoder:
        enabled=True;timeout_seconds=2
        async def embed(self,query):core.entities.revoke(entity['id'],2);return [1.,0.]
    core.memory.embedder=Encoder()
    assert retrieve(core,'记忆工坊',use_entities=True)[0]==[]


def test_historical_query_cannot_use_new_alias(core):
    fixture(core);remember(core,'Memoria使用快照恢复。')
    rows,diag=retrieve(core,'记忆工坊',use_entities=True,as_of='2020-01-01T00:00:00+00:00')
    assert not rows and 'entity' not in diag['lanes']


def test_entity_dependency_checked_after_reader_before_publish(core):
    from memoria.prompting.assembler import _parse_frame
    entity=fixture(core);remember(core,'Memoria使用快照恢复。')
    async def reader(messages,**kwargs):
        _,sections=_parse_frame(messages[1]['content'])
        packet=json.loads(next(s.content for s in sections if s.name=='Structured State').rsplit('\n',1)[-1])
        core.entities.revoke(entity['id'],2)
        return ChatResult(content=json.dumps({'status':'answer','answer':'快照恢复','evidence_ids':packet['allowed_evidence_ids']}))
    core.llm.chat=reader
    result=asyncio.run(core.answer(QueryRequest(query='记忆工坊',mode='rrf',use_entities=True,include_experiences=False)))
    assert result['status']=='abstain' and result['validation']['reason']=='entity_binding_changed'


def test_reader_receives_literal_alias_bridge_with_source_and_budget(core):
    from memoria.prompting.assembler import _parse_frame
    fixture(core);memory,_=remember(core,'Memoria使用快照恢复。')
    async def reader(messages,**kwargs):
        _,sections=_parse_frame(messages[1]['content'])
        packet=json.loads(next(s.content for s in sections if s.name=='Structured State').rsplit('\n',1)[-1])
        bridge=packet['evidence'][0]['data']['entity_identity'][0]
        assert bridge['canonical_name']=='Memoria'
        assert any(e['quote']=='Memoria又名记忆工坊。' and e['source_id'] for e in bridge['evidence'])
        return ChatResult(content=json.dumps({'status':'answer','answer':'快照恢复','evidence_ids':packet['allowed_evidence_ids']}))
    core.llm.chat=reader
    result=asyncio.run(core.answer(QueryRequest(query='记忆工坊',mode='rrf',use_entities=True,include_experiences=False,budget_chars=8000)))
    assert result['status']=='answer' and result['citations'][0]['identity_evidence']
    record=asyncio.run(core.search(MemorySearch(query='记忆工坊',domains=['memory'],use_entities=True,budget_chars=8000)))
    assert record['evidence'][0]['provenance']['entity_identity']


def test_entity_seed_is_not_lost_to_global_scan_limit(core):
    fixture(core);memory,_=remember(core,'Memoria使用快照恢复。')
    for i in range(105):core.store.add_memory('无关工程占位'+str(i))
    core.memory.vector_scan_limit=100
    rows,_=retrieve(core,'记忆工坊',use_entities=True)
    assert memory['id'] in [r['id'] for r in rows]
    assert not retrieve(core,'记忆工坊',use_entities=True,kinds={'goal'})[0]


def test_entity_watermark_and_alias_cohort_normalization(core):
    from tests.test_source_formation import model as source_model,form
    from memoria.memory.source_formation import OBSERVE
    fixture(core)
    def alter(stage,data,output,index):
        if stage==OBSERVE and '记忆工坊' in data['source']:
            row=output['observations'][0];row['subject']='记忆工坊';row['content']='据资料，记忆工坊部署需核对配置。'
    source_model(core,alter=alter)
    sources=[core.ingest(SourceInput(kind='document',title='fixture',content=name+'部署需核对配置。第'+str(i)+'条',authorship='external'))
        for i,name in enumerate(['Memoria','记忆工坊','Memoria'])]
    for source in sources:receipt=form(core.library.formation,source)
    assert receipt['consolidation']['status']=='approved'
    groups=[json.loads(r[0]) for r in core.store.db.execute('SELECT group_json FROM library_observations')]
    assert len({g['subject'] for g in groups})==1 and groups[0]['subject'].startswith('entity:')
    assert core.store.db.execute("SELECT generation FROM memory_trace_epochs WHERE stage='entity'").fetchone()[0]>0


def test_document_name_after_first_four_thousand_characters_is_indexed(core):
    fixture(core)
    text='隔离工程运行日志。'*600+'Memoria的恢复步骤是使用快照。'
    item=core.ingest(SourceInput(kind='document',title='长记录',content=text,authorship='external'))
    result=asyncio.run(core.search(MemorySearch(query='记忆工坊',domains=['library'],use_entities=True,budget_chars=4000)))
    assert result['evidence'] and 'Memoria' in result['evidence'][0]['content']
    assert result['budget']['used']<=4000
    assert result['evidence'][0]['start']>4000
