"""Literal identity/quote constraints. Fictional data, no production writes or paid calls."""
import asyncio,json
import pytest
from memoria.claims import sha
from memoria.memory.access import QueryRequest
from memoria.memory.entities import Anchor
from memoria.memory.library import SourceInput
from memoria.memory.records import MemorySearch
from memoria.memory.source_windows import windows
from memoria.llm.types import ChatResult
from memoria.prompting.assembler import _parse_frame
from tests.test_memory_kernel import core
from tests.test_entities import register,message,anchor
from tests.test_entity_retrieval import fixture,remember
from tests.test_auto_memory_review import env,model,candidate


@pytest.mark.parametrize('enabled',[False,True])
def test_known_wrong_identity_cannot_reach_selection_even_without_entity_lane(core,enabled):
    fixture(core);register(core,name='记忆工坊',kind='person')
    good,_=remember(core,'Memoria的工作方式是先快照再校验。')
    wrong,_=remember(core,'记忆工坊的工作方式是先拍照再散步。')
    result=asyncio.run(core.access(QueryRequest(query='记忆工坊的工作方式是什么',entity_type='project',
        mode='rrf',use_entities=enabled,include_experiences=False)))
    assert wrong['id'] not in {c['id'] for c in result['candidates']}
    assert any(r['id']==wrong['id'] and r['reason']=='entity_identity_ambiguous' for r in result['rejected'])
    if enabled:assert good['id'] in {c['id'] for c in result['selection']['payload']['evidence']}


def test_missing_identity_is_unestablished_not_conflicting(core):
    fixture(core);item,_=remember(core,'工作方式是先校验。')
    result=asyncio.run(core.access(QueryRequest(query='记忆工坊工作方式',mode='rrf',use_entities=False,include_experiences=False)))
    assert {'id':item['id'],'reason':'entity_identity_unestablished'} in result['rejected']
    assert result['path']=='clarify'


def test_identity_binding_works_without_index_and_revocation_invalidates_it(core):
    entity=fixture(core);source=message(core,'Memoria采用增量快照。')
    lookup=core.entities.lookup('记忆工坊')
    bound=core.entities.source_binding(lookup,anchor(source))
    assert bound['status']=='supported_literal_identity' and core.entities.dependency_valid(bound['dependency'])
    assert core.store.db.execute('SELECT COUNT(*) FROM memory_entity_mentions').fetchone()[0]==0
    proof=core.entities.evidence(bound['dependency'])
    assert any(e['quote']=='Memoria又名记忆工坊。' for i in proof for e in i['evidence'])
    core.store.undo_memory_sources([source['id']])
    assert not core.entities.dependency_valid(bound['dependency'])


def test_two_windows_are_exact_original_slices_not_derived_offsets(core):
    text='诊断前请勿删除备份。'+'例行记录。'*1300+'Memoria的恢复策略是先快照恢复，再校验哈希。'
    source=message(core,text)
    item=core.store.add_memory('快照恢复与哈希校验',source_ref=source['id'],require_source=True)
    result=asyncio.run(core.access(QueryRequest(query='快照恢复与哈希校验',mode='rrf',include_experiences=False,budget_chars=8000)))
    chosen=next(c for c in result['selection']['payload']['evidence'] if c['id']==item['id'])
    data=chosen['data'];assert data['source_window']['partial']
    assert '先快照恢复，再校验哈希' in data['text'] and len(data['text'])<1900
    for e in [data['evidence']]+data['support_evidence']:
        assert e['quote']==text[e['start']:e['end']] and e['content_sha256']==sha(text)
    assert result['selection']['cost']<=8000


def test_complementary_windows_preserve_negation_and_parent_identity():
    text='恢复时不得删除原始快照。'+'隔离记录。'*900+'发布后必须校验完整哈希。'
    ranges=windows(text,'恢复快照与发布校验哈希',width=500)
    assert len(ranges)==2
    quotes=[text[a:b] for a,b in ranges]
    assert any('不得删除原始快照' in q for q in quotes) and any('必须校验完整哈希' in q for q in quotes)
    assert all(0<=a<b<=len(text) and b-a<=500 for a,b in ranges)


def test_ambiguity_and_past_identity_cannot_be_overridden_by_explicit_subject(core):
    fixture(core);register(core,name='记忆工坊',kind='person')
    result=asyncio.run(core.access(QueryRequest(query='记忆工坊',subject_id='entity:explicit',mode='rrf',include_experiences=False)))
    assert result['path']=='clarify' and result['reason'].startswith('ambiguous_entity_identity')
    result=asyncio.run(core.access(QueryRequest(query='记忆工坊',subject_id='entity:explicit',entity_type='project',
        fact_as_of='2020-01-01T00:00:00+00:00',mode='rrf',include_experiences=False)))
    assert result['path']=='clarify' and result['reason'].startswith('historical_entity_identity_not_established')


def test_library_alias_proof_is_shared_safety_when_entity_recall_off(core):
    fixture(core);register(core,name='其他项目')
    good=core.ingest(SourceInput(kind='document',title='恢复记录',content='Memoria以快照恢复。',authorship='external'))
    wrong=core.ingest(SourceInput(kind='document',title='恢复记录',content='其他项目以日志恢复。',authorship='external'))
    result=asyncio.run(core.search(MemorySearch(query='记忆工坊恢复',domains=['library'],use_entities=False)))
    assert result['evidence'] and all(e['source_id']==good['id'] for e in result['evidence'])
    assert result['evidence'][0]['provenance']['entity_identity']
    assert any(r['id']==wrong['id'] and r['reason']=='entity_identity_incompatible' for r in result['selection']['identity_rejected'])


def test_identity_diagnostics_cannot_overrun_complete_source_packet_budget(core):
    fixture(core);register(core,name='其他项目')
    for i in range(8):
        core.ingest(SourceInput(kind='document',title=f'恢复记录{i}',content=f'其他项目恢复记录编号{i}。',authorship='external'))
    capture={}
    packet=asyncio.run(core.search(MemorySearch(query='记忆工坊恢复记录',domains=['library'],use_entities=False,budget_chars=800),capture=capture))
    assert not packet['evidence'] and packet['budget']['used']<=800
    assert packet['selection']['identity_rejected_count']==8 and len(capture['identity_rejected'])==8
    assert packet['selection']['identity_diagnostics_truncated']


def test_risk_checks_receive_literal_alias_and_recheck_revocation(core):
    entity=fixture(core);remember(core,'Memoria以快照恢复。');calls=[]
    async def model(messages,**kw):
        system=messages[0]['content'];calls.append(system)
        if system.startswith('Assess'):
            packet=json.loads(messages[1]['content']);item=packet['evidence'][0]
            assert 'Memoria又名记忆工坊。' in item['source_texts']
            quote=next(q for q in item['source_texts'] if '快照' in q)
            return ChatResult(content=json.dumps({'state':'sufficient','requirements':[{'fact':'恢复',
                'status':'supported','sources':[{'id':item['id'],'quote':quote}]}],'missing_queries':[],'reason':'Direct'}))
        if system.startswith('Verify'):
            item=json.loads(messages[1]['content'])['evidence'][0]
            assert 'Memoria又名记忆工坊。' in item['source_texts']
            core.entities.revoke(entity['id'],2)
            return ChatResult(content=json.dumps({'verdict':'supported','claims':[{'statement':'快照恢复',
                'relation':'supported','basis':'explicit','sources':[{'id':item['id'],'quote':next(q for q in item['source_texts'] if '快照' in q)}],
                'reason':'Direct'}],'missing_queries':[],'reason':'Direct'}))
        _,sections=_parse_frame(messages[1]['content'])
        packet=json.loads(next(s.content for s in sections if s.name=='Structured State').rsplit('\n',1)[-1])
        return ChatResult(content=json.dumps({'status':'answer','answer':'快照恢复','evidence_ids':packet['allowed_evidence_ids']}))
    core.llm.chat=model
    result=asyncio.run(core.answer(QueryRequest(query='记忆工坊怎么恢复',mode='rrf',use_entities=True,
        include_experiences=False,use_answer_risk=True)))
    assert len(calls)==3 and result['status']=='abstain' and result['validation']['reason']=='entity_binding_changed'


@pytest.mark.parametrize('decision',['review','reject'])
def test_negative_model_judgment_cannot_hide_literal_pending_conflict(env,decision):
    _,_,store,service=env;source,first=candidate(store)
    model(service,source['content']);asyncio.run(service.auto_review.review(first['id']))
    source,new=candidate(store,'我通常用英文交流。','用户通常用英文交流。')
    model(service,source['content'],changes={'decision':decision,'assessment':'Contradicted',
        'claim':{'predicate':'response_language','value':'英文','scope':{},'polarity':'positive'}})
    row=asyncio.run(service.auto_review.review(new['id']))
    assert row['status']=='pending' and row['assessment']['assessment']=='Contradicted'
    assert row['assessment']['reason_codes']==['literal_conflict_requires_review']
    assert not row['assessment']['structured_proposal']['automatic_approval']
    assert len(service.claims.list())==1 and service.claims.list()[0]['value']=='中文'
    result=asyncio.run(service.kernel.answer(QueryRequest(query='我的回答语言是什么？')))
    assert result['status']=='clarify' and not result['reader_called']
    store.undo_memory_sources([source['id']])
    assert not asyncio.run(service.kernel.access(QueryRequest(query='我的回答语言是什么？'))).get('pending_revisions')


@pytest.mark.parametrize('text,value',[('如果以后我要用英文交流。','英文'),('我同事通常用英文交流。','英文'),('我通常用中文交流。','英文')])
def test_literal_hold_cannot_override_missing_negation_subject_or_conditions(env,text,value):
    _,_,store,service=env;source,first=candidate(store)
    model(service,source['content']);asyncio.run(service.auto_review.review(first['id']))
    source,new=candidate(store,text,'用户通常用英文交流。')
    model(service,source['content'],changes={'decision':'reject','assessment':'Contradicted',
        'claim':{'predicate':'response_language','value':value,'scope':{},'polarity':'positive'}})
    result=asyncio.run(service.auto_review.review(new['id']))
    assert result['status']=='rejected' and not result['assessment'].get('structured_proposal')
