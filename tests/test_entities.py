"""Source-bound entity tests use isolated fictional engineering inputs only."""
import asyncio,json
import pytest
from fastapi.testclient import TestClient
from memoria.claims import sha
from memoria.memory.entities import Anchor,EntityInput,AliasInput,spans
from memoria.memory.library import LibraryError,SourceInput,RevisionInput
from tests.test_memory_kernel import core
from tests.test_memory_formation import env,model,send,observer


def message(kernel,text,session=None):
    source=kernel.store.add_message(session or kernel.store.create_session()['id'],'user',text)
    return source

def anchor(source,quote=None):
    text=source['content'];quote=quote or text;at=text.index(quote)
    return Anchor(domain='conversation',source_id=source['id'],start=at,end=at+len(quote),quote=quote,sha256=sha(text))

def register(kernel,name='Memoria',kind='project',**kwargs):
    source=message(kernel,name)
    return kernel.entities.create(EntityInput(canonical_name=name,entity_type=kind,evidence=anchor(source),**kwargs)),source


def test_alias_source_binding_same_name_ambiguity_and_type_constraint(core):
    entity,_=register(core)
    statement=message(core,'Memoria又名记忆工坊。')
    revised=core.entities.alias(entity['id'],AliasInput(expected_revision=1,name='记忆工坊',evidence=anchor(statement)))
    assert revised['revision']==2
    assert core.entities.match('记忆工坊的架构')['matches'][0]['entity_id']==entity['id']
    other,_=register(core,kind='person')
    found=core.entities.match('Memoria')
    assert not found['matches'] and len(found['ambiguous'][0]['entity_ids'])==2
    assert core.entities.match('Memoria',entity_type='project')['matches'][0]['entity_id']==entity['id']
    assert entity['id']!=other['id']

@pytest.mark.parametrize('text',['Memoria不是记忆工坊。','Memoria和记忆工坊是不同项目。','Memoria与记忆工坊很相似。'])
def test_similarity_negation_and_cooccurrence_cannot_approve_alias(core,text):
    entity,_=register(core);source=message(core,text)
    with pytest.raises(LibraryError):core.entities.alias(entity['id'],AliasInput(expected_revision=1,name='记忆工坊',evidence=anchor(source)))
    assert core.entities.get(entity['id'])['revision']==1


def test_relative_alias_is_session_bound_and_revocation_is_fresh(core):
    entity,_=register(core);source=message(core,'Memoria又名我的长期记忆项目。')
    with pytest.raises(LibraryError):core.entities.alias(entity['id'],AliasInput(expected_revision=1,name='我的长期记忆项目',evidence=anchor(source)))
    body=AliasInput(expected_revision=1,name='我的长期记忆项目',scope='working',session_id=source['session_id'],evidence=anchor(source))
    core.entities.alias(entity['id'],body)
    assert core.entities.match('我的长期记忆项目')['matches']==[]
    assert core.entities.match('我的长期记忆项目',session_id=source['session_id'])['matches']
    core.store.undo_memory_sources([source['id']])
    assert core.entities.match('我的长期记忆项目',session_id=source['session_id'])['matches']==[]
    assert core.entities.match('Memoria')['matches']
    with pytest.raises(LibraryError):core.entities.revoke(entity['id'],1)
    core.entities.revoke(entity['id'],2)
    assert not core.entities.match('Memoria')['matches']


def test_source_hash_role_public_origin_and_scope_cannot_be_bypassed(core):
    source=message(core,'Memoria')
    bad=anchor(source).model_copy(update={'sha256':'0'*64})
    with pytest.raises(LibraryError):core.entities.create(EntityInput(canonical_name='Memoria',entity_type='project',evidence=bad))
    core.store.db.execute("UPDATE messages SET role='assistant' WHERE id=?",(source['id'],));core.store.db.commit()
    with pytest.raises(LibraryError):core.entities.create(EntityInput(canonical_name='Memoria',entity_type='project',evidence=anchor(source)))
    sid=core.store.create_session()['id']
    item=core.ingest(SourceInput(kind='document',title='project',content='Memoria',scope='working',session_id=sid))
    evidence=Anchor(domain='library',source_id=item['id'],revision_id=item['revision']['id'],start=0,end=7,quote='Memoria',sha256=sha('Memoria'))
    with pytest.raises(LibraryError):core.entities.create(EntityInput(canonical_name='Memoria',entity_type='project',evidence=evidence))
    entity=core.entities.create(EntityInput(canonical_name='Memoria',entity_type='project',scope='working',session_id=sid,evidence=evidence))
    assert not core.entities.match('Memoria')['matches']
    assert core.entities.match('Memoria',session_id=sid)['matches']
    core.library.revise(item['id'],RevisionInput(expected_revision_id=item['revision']['id'],title='new',content='Other'))
    assert not core.entities.match('Memoria',session_id=sid)['matches']


def test_unicode_original_offsets_and_english_boundaries():
    text='项目Ｍｅｍｏｒｉａ部署；Memorial不匹配。'
    assert spans(text,'Memoria')==[(2,9)]
    a,b=spans('Straße','STRASSE')[0]
    assert 'Straße'[a:b]=='Straße'


def test_automatic_formation_and_approved_memory_record_entity_mentions(env):
    store,service,_=env;kernel=service.kernel
    entity,_=register(kernel)
    text='Memoria项目采用来源核验机制。'
    model(service,specs={text:observer(text,category='fact',asserted=True,durable=True)})
    source,job=send(store,service,text)
    assert job['status']=='completed' and len(store.memories())==1
    memory=store.memories()[0]
    assert kernel.entities.signature('memory',memory['id'],sha(memory['content']))==[entity['id']]
    oid=store.db.execute('SELECT id FROM memory_observations').fetchone()[0]
    assert kernel.entities.signature('observation',oid,sha(text))==[entity['id']]
    store.undo_memory_sources([source['id']])
    assert not kernel.entities.signature('memory',memory['id'],sha(memory['content']))


def test_entity_identity_prevents_cross_project_pattern_and_revocation_retires_pattern(env):
    store,service,_=env;kernel=service.kernel
    a,_=register(kernel);b,_=register(kernel,'Orion')
    model(service)
    for text in ('Memoria资料甲','Orion资料乙','Memoria资料丙'):send(store,service,text)
    assert store.memories()==[]
    send(store,service,'Memoria资料丁')
    assert len(store.memories())==1
    kernel.entities.revoke(a['id'],1)
    service.formation.maintenance()
    assert store.retrieval_memories()==[]


def test_source_observations_join_same_entity_cohort_and_old_binding_exits(env):
    from tests.test_source_formation import model as source_model,form
    store,service,_=env;kernel=service.kernel;entity,_=register(kernel)
    alias_source=message(kernel,'Memoria又名记忆工坊。')
    kernel.entities.alias(entity['id'],AliasInput(expected_revision=1,name='记忆工坊',evidence=anchor(alias_source)))
    source_model(service)
    docs=[kernel.ingest(SourceInput(kind='document',title='engineering',content='Memoria部署需要核对配置。来源'+str(i),authorship='external')) for i in range(3)]
    for item in docs:form(kernel.library.formation,item)
    groups=[json.loads(r[0]) for r in store.db.execute('SELECT group_json FROM library_observations')]
    assert all(g['entity_ids']==[entity['id']] for g in groups)
    oid=store.db.execute('SELECT id FROM library_observations LIMIT 1').fetchone()[0]
    observation=kernel.library.formation._observation(oid)
    assert kernel.library.formation._live(observation)
    kernel.entities.revoke(entity['id'],2)
    assert not kernel.library.formation._live(observation)


def test_entity_api_is_tenant_bound_and_rejects_foreign_or_forged_owner(core,tmp_path):
    from tests.test_memory_kernel import config_at
    client=TestClient(__import__('memoria.api.app',fromlist=['_create_tenant_app'])._create_tenant_app(config_at(tmp_path/'other')))
    try:
        source=message(client.app.state.service.kernel,'Memoria')
        body={'canonical_name':'Memoria','entity_type':'project','evidence':anchor(source).model_dump()}
        assert client.post('/api/memory-kernel/entities',json=body|{'user_id':'forged'}).status_code==422
        response=client.post('/api/memory-kernel/entities',json=body)
        assert response.status_code==201
        assert client.get('/api/memory-kernel/entities/'+response.json()['id']).status_code==200
        assert core.entities.match('Memoria')['matches']==[]
    finally:client.app.state.store.close()
