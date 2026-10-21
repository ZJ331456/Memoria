"""Synthetic history association/fault tests; no real user or research labels."""
import asyncio
import json

import pytest

from memoria.llm.types import ChatResult
from memoria.memory import formation_history as history
from memoria.memory.formation_store import with_entities, bucket
from tests.test_memory_formation import env, model, observer, send


def seed(store, service, text='我维护星桥项目，采用模块化架构。', session=None):
    model(service,specs={text:observer(text,category='fact',content=text,asserted=True,durable=True)})
    source,_=send(store,service,text,session)
    memory=next(m for m in store.memories() if m['source_ref']==source['id'])
    return source,memory


def linking(service,mid,*,status='Supported',relation='new_event',mutate=None,bad_id=False):
    base=model(service)
    original=service.llm.chat
    captured=[]
    async def chat(messages,**kwargs):
        prompt=messages[0]['content'];data=json.loads(messages[1]['content'])
        captured.append((prompt,data))
        if '个人记忆的观察器' in prompt:
            output={'observations':[observer(data['source'],category='fact',content=data['source'],asserted=True,durable=True,
                linked_memory_ids=[mid if not bad_id else 'not-in-supplied-pool'],subject_ref='memory:'+mid,relation=relation)]}
        elif '独立的记忆关联核验器' in prompt:
            target=next(r for r in data['history_context']['memories'] if r['id']==mid)
            output={'links':[{'index':p['index'],'assessment':status,'current_quote':p['quote'],
                'current_fact_supported':status=='Supported','subject_supported':True,'conditions_preserved':True,
                'identity_quote':'星桥项目' if '星桥项目' in p['quote'] else '',
                'target_quotes':{'memory:'+mid:target['source_excerpt']['text']}} for p in data['proposals']]}
        else:
            return await original(messages,**kwargs)
        if mutate:mutate(prompt,output,data)
        return ChatResult(content=json.dumps(output,ensure_ascii=False),raw_message={},usage={'total_tokens':19})
    service.llm.chat=chat
    return captured


def test_actual_related_memories_and_current_evidence_are_separate(env):
    store,service,client=env
    session=store.create_session()['id']
    source,old=seed(store,service,session=session)
    calls=linking(service,old['id'])
    text='这个项目新增了故障回滚模块。'
    current,job=send(store,service,text,session)
    assert job['status']=='completed' and job['extracted_count']==1
    new=next(m for m in store.memories() if m['source_ref']==current['id'])
    assert new['content']==text and store.memory(old['id'])['status']=='active'
    assert store.memory_source(new['id'])['message_id']==current['id']
    row=next(r for r in client.get('/api/memory-observations').json() if r['message_id']==current['id'])
    assert row['history']['linked_memory_ids']==[old['id']]
    assert row['history']['association']['status']=='verified'
    observe=calls[0][1]
    assert observe['source']==text
    assert source['id'] in {r['id'] for r in observe['history_context']['recent_messages']}
    assert len(json.dumps(observe['history_context'],ensure_ascii=False))<=history.MAX_CHARS
    assert any('独立的记忆关联核验器' in p for p,_ in calls)
    assert not service.kernel.entities.cohort('memory',new['id'],history.digest(new['content']))


@pytest.mark.parametrize('status',['Insufficient','Contradicted'])
def test_negative_link_audit_preserves_unknown_observation_not_fact(env,status):
    store,service,_=env
    session=store.create_session()['id'];_,old=seed(store,service,session=session)
    linking(service,old['id'],status=status)
    current,job=send(store,service,'这个项目新增了回滚模块。',session)
    assert job['status']=='completed' and job['extracted_count']==0
    assert not [m for m in store.memories() if m['source_ref']==current['id']]
    row=store.db.execute('SELECT * FROM memory_observations WHERE message_id=?',(current['id'],)).fetchone()
    assert history.load(store,row['id'])['audit']['status']=='unresolved'


def test_unknown_proposed_memory_id_is_rejected_before_audit(env):
    store,service,_=env
    session=store.create_session()['id'];_,old=seed(store,service,session=session)
    calls=linking(service,old['id'],bad_id=True)
    current,job=send(store,service,'这个项目增加了回滚。',session)
    assert job['status']=='retry' and len(calls)==1
    assert store.db.execute('SELECT count(*) FROM memory_observations WHERE message_id=?',(current['id'],)).fetchone()[0]==0


def test_cross_session_vector_similarity_cannot_resolve_a_pronoun(env):
    store,service,_=env
    _,old=seed(store,service)
    linking(service,old['id'])
    _,job=send(store,service,'这个项目新增了回滚模块。')
    assert job['status']=='completed' and job['extracted_count']==0


def test_two_projects_are_ambiguous_even_if_model_says_supported(env):
    store,service,_=env
    session=store.create_session()['id'];_,old=seed(store,service,session=session)
    store.add_message(session,'user','我还维护另一个项目，名字叫海岚。')
    linking(service,old['id'])
    _,job=send(store,service,'这个项目增加了回滚模块。',session)
    assert job['status']=='completed' and job['extracted_count']==0


def test_future_assistant_revoked_and_other_session_messages_not_in_context(env):
    store,service,_=env
    session=store.create_session()['id'];model(service)
    earlier=store.add_message(session,'user','我维护星桥项目。')
    assistant=store.add_message(session,'assistant','建议你采用分层架构。')
    current=store.add_message(session,'user','这个项目新增了备份模块。')
    future=store.add_message(session,'user','我后来将项目更名了。')
    other=store.add_message(store.create_session()['id'],'user','另一个用户空间内的句子')
    context=asyncio.run(history.collect(store,service.kernel.memory,current))
    ids={r['id'] for r in context['recent_messages']}
    assert ids=={earlier['id']} and not ids & {assistant['id'],future['id'],other['id']}
    store.undo_memory_sources([earlier['id']])
    assert asyncio.run(history.collect(store,service.kernel.memory,current))['recent_messages']==[]


def test_incompatible_conditions_and_later_recorded_memory_are_excluded(env):
    store,service,_=env
    source=store.add_message(store.create_session()['id'],'user','面试模拟时，我维护星桥项目的英文文档。')
    old=store.add_memory(source['content'],source_ref=source['id'],require_source=True)
    current=store.add_message(store.create_session()['id'],'user','我维护星桥项目的架构。')
    context=asyncio.run(history.collect(store,service.kernel.memory,current))
    assert not context['memories']
    with store.db:store.db.execute('UPDATE memories SET updated_at=? WHERE id=?',('2099-01-01T00:00:00+00:00',old['id']))
    context=asyncio.run(history.collect(store,service.kernel.memory,current))
    assert not context['memories']


@pytest.mark.parametrize('stage',['observe','associate','review','embedding'])
def test_linked_source_change_during_model_or_embedding_cannot_publish(env,stage):
    store,service,_=env
    session=store.create_session()['id'];source,old=seed(store,service,session=session)
    changed=False
    def change():
        nonlocal changed
        if not changed:
            changed=True
            with store.db:store.db.execute('UPDATE messages SET content=? WHERE id=?',('源已撤回原来的主体信息',source['id']))
    def mutate(prompt,output,data):
        if stage=='observe' and '个人记忆的观察器' in prompt:change()
        if stage=='associate' and '独立的记忆关联核验器' in prompt:change()
    linking(service,old['id'],mutate=mutate)
    original=service.llm.chat
    async def wrapped(messages,**kwargs):
        result=await original(messages,**kwargs)
        if stage=='review' and '个人记忆的观察器' not in messages[0]['content'] and '独立的记忆关联核验器' not in messages[0]['content']:change()
        return result
    service.llm.chat=wrapped
    if stage=='embedding':
        class Enabled:enabled=True
        service.kernel.memory.embedder=Enabled()
        async def embed(text):change();return None,None
        service.kernel.memory._embed_document=embed
        # Do not invoke the fake embedder during context recall.
        retrieve=service.kernel.memory.retrieve
        async def recall(*a,**kw):
            saved=service.kernel.memory.embedder;service.kernel.memory.embedder=None
            try:return await retrieve(*a,**kw)
            finally:service.kernel.memory.embedder=saved
        service.kernel.memory.retrieve=recall
    current,job=send(store,service,'这个项目新增了备份模块。',session)
    assert changed and not [m for m in store.memories() if m['source_ref']==current['id']]


def test_relation_proposal_does_not_supersede_old_memory(env):
    store,service,_=env
    session=store.create_session()['id'];_,old=seed(store,service,session=session)
    linking(service,old['id'],relation='revision_candidate')
    current,job=send(store,service,'这个项目采用事件架构。',session)
    assert job['status']=='completed'
    review=next(r for r in store.memory_reviews('all') if r['source_ref']==current['id'])
    assert review['status']=='pending'
    assert 'history_revision_requires_review' in review['assessment']['reason_codes']
    assert store.memory(old['id'])['status']=='active'


def test_unknown_project_observations_never_share_a_pattern_bucket(env):
    store,service,_=env;model(service)
    a,_=send(store,service,'查找这个项目的部署文档')
    b,_=send(store,service,'查找这个项目的部署文档')
    rows=[store.db.execute('SELECT * FROM memory_observations WHERE message_id=?',(s['id'],)).fetchone() for s in (a,b)]
    assert all(r is not None for r in rows)
    assert bucket(with_entities(store,rows[0]))!=bucket(with_entities(store,rows[1]))


def test_cached_links_revalidate_versions_and_read_api_hides_stale_links(env):
    store,service,client=env
    session=store.create_session()['id'];source,old=seed(store,service,session=session)
    calls=linking(service,old['id'])
    current,job=send(store,service,'这个项目新增了备份模块。',session)
    count=len(calls)
    raw_job=dict(store.db.execute('SELECT * FROM memory_jobs WHERE id=?',(job['id'],)).fetchone())
    cached=asyncio.run(service.formation.extract(raw_job))
    assert len(calls)==count and cached['observations'][0]['linked_memory_ids']==[old['id']]
    with store.db:store.db.execute('UPDATE messages SET content=? WHERE id=?',('不再是原来那条项目描述',source['id']))
    with pytest.raises(ValueError):asyncio.run(service.formation.extract(raw_job))
    assert not [r for r in client.get('/api/memory-observations').json() if r['message_id']==current['id']]
    # The independently supported literal new fact is not the old project's fact.
    new=next(m for m in store.memories() if m['source_ref']==current['id'])
    assert store.memory_source(new['id'])['message_id']==current['id']


def test_prior_topics_do_not_include_future_observations(env):
    store,service,_=env;model(service)
    current=store.add_message(store.create_session()['id'],'user','查找数据库资料')
    send(store,service,'查找未来的开发资料')
    context=asyncio.run(history.collect(store,service.kernel.memory,current))
    assert context['prior_topics']==[]


def test_history_model_payload_has_hard_character_and_item_bounds(env):
    store,service,_=env;session=store.create_session()['id']
    for i in range(12):store.add_message(session,'user',f'第{i}条项目资料。'+'纯文本内容'*300)
    current=store.add_message(session,'user','这个项目的说明是什么？')
    context=asyncio.run(history.collect(store,service.kernel.memory,current))
    assert len(context['recent_messages'])<=4 and len(context['memories'])<=4
    assert len(json.dumps(history.project(context),ensure_ascii=False))<=history.MAX_CHARS


def test_unknown_reference_is_valid_input_and_never_guesses_subject(env):
    store,service,client=env
    text='这个项目新增了备份。'
    model(service,specs={text:observer(text,asserted=True,durable=True,relation='unknown')})
    current,job=send(store,service,text)
    assert job['status']=='completed' and job['extracted_count']==0
    row=next(r for r in client.get('/api/memory-observations').json() if r['message_id']==current['id'])
    assert row['history']['subject_ref']=='unknown' and row['history']['association']['status']=='unresolved'


def test_bad_history_id_isolated_from_an_independent_valid_observation(env):
    store,service,_=env
    text='我维护星桥项目。我从事后端开发。'
    base=model(service)
    async def chat(messages,**kwargs):
        return ChatResult(content=json.dumps({'observations':[
            observer('我维护星桥项目。',linked_memory_ids=['invented-id']),
            observer('我从事后端开发。',category='fact',content='我从事后端开发。',asserted=True,durable=True)]},ensure_ascii=False),raw_message={})
    # Manual mode isolates extraction; it does not count as real admission.
    service.auto_review.mode='manual';service.llm.chat=chat
    current,job=send(store,service,text)
    assert job['status']=='completed' and job['extracted_count']==1
    diagnostic=json.loads(store.db.execute('SELECT diagnostics_json FROM memory_extraction_diagnostics').fetchone()[0])
    assert diagnostic['accepted_indices']==[1]
    assert diagnostic['rejected'][0]['reason']=='history_id_not_supplied_or_duplicate'


def test_default_observation_cannot_link_an_interview_condition(env):
    from memoria.memory.formation import Observation
    current={'id':'test','content':'平时用中文，面试模拟时用英文。','session_id':'session'}
    context={'memories':[{'id':'old','scopes':['{"activity":"interview"}']}],'recent_messages':[]}
    item=Observation.model_validate(observer('平时用中文',content='中文',linked_memory_ids=['old']))
    with pytest.raises(ValueError,match='history_condition_mismatch'):history.proposal_check(item,context,current)
    item=Observation.model_validate(observer('面试模拟时用英文',content='英文',linked_memory_ids=['old']))
    history.proposal_check(item,context,current)


def test_maintaining_a_project_does_not_create_a_goal_claim(env):
    store,service,_=env
    text='我维护星桥项目。'
    def mutate(stage,output,data):
        if stage=='review':output['claim']={'predicate':'project_goal','value':'星桥项目','scope':{}}
    model(service,specs={text:observer(text,category='fact',content=text,asserted=True,durable=True)},mutate=mutate)
    _,job=send(store,service,text)
    assert job['status']=='completed' and len(store.memories())==1
    assert store.db.execute('SELECT count(*) FROM claim_roots').fetchone()[0]==0


def test_entity_lookup_change_while_observing_blocks_association(env):
    from memoria.memory.entities import EntityInput,Anchor
    store,service,_=env
    session=store.create_session()['id'];source,old=seed(store,service,session=session)
    def mutate(prompt,output,data):
        if '个人记忆的观察器' in prompt:
            start=source['content'].index('星桥项目')
            service.kernel.entities.create(EntityInput(canonical_name='星桥项目',entity_type='project',
                evidence=Anchor(domain='conversation',source_id=source['id'],start=start,end=start+4,
                    quote='星桥项目',sha256=history.digest(source['content']))))
    linking(service,old['id'],mutate=mutate)
    current,job=send(store,service,'星桥项目新增了备份模块。',session)
    assert job['status']=='retry'
    assert not store.db.execute('SELECT 1 FROM memory_observations WHERE message_id=?',(current['id'],)).fetchone()


def test_third_party_and_sensitive_messages_are_not_identity_antecedents(env):
    store,service,_=env
    session=store.create_session()['id']
    store.add_message(session,'user','我同事维护星桥项目。')
    store.add_message(session,'user','我有相关病史。')
    current=store.add_message(session,'user','这个项目新增备份。')
    context=asyncio.run(history.collect(store,service.kernel.memory,current))
    assert context['recent_messages']==[] and context['memories']==[]


@pytest.mark.parametrize('mutation',['withdraw','memory_version','support','validity','entity_catalog'])
def test_identity_dependent_pattern_stops_before_maintenance(env,mutation):
    from memoria.memory.entities import EntityInput,Anchor
    store,service,_=env
    source,old=seed(store,service)
    for text in ('星桥项目部署记录甲','星桥项目部署记录乙','星桥项目部署记录丙'):
        linking(service,old['id'])
        _,job=send(store,service,text)
        assert job['status']=='completed'
    pattern=store.db.execute("SELECT * FROM memory_pattern_versions WHERE status='active'").fetchone()
    assert pattern and pattern['memory_id'] in {m['id'] for m in store.retrieval_memories()}
    if mutation=='withdraw':store.undo_memory_sources([source['id']])
    elif mutation=='memory_version':
        with store.db:store.db.execute('UPDATE memories SET updated_at=? WHERE id=?',('2099-01-01T00:00:00+00:00',old['id']))
    elif mutation=='support':
        with store.db:store.db.execute("UPDATE memory_support_evidence SET state='invalidated' WHERE group_id IN (SELECT id FROM memory_support_groups WHERE memory_id=?)",(old['id'],))
    elif mutation=='validity':
        with store.db:store.db.execute('UPDATE memory_validity_intervals SET invalid_at=valid_at WHERE memory_id=?',(old['id'],))
    else:
        start=source['content'].index('星桥项目')
        service.kernel.entities.create(EntityInput(canonical_name='星桥项目',entity_type='project',
            evidence=Anchor(domain='conversation',source_id=source['id'],start=start,end=start+4,quote='星桥项目',sha256=history.digest(source['content']))))
    assert pattern['memory_id'] not in {m['id'] for m in store.retrieval_memories()}
    assert not service.formation.core()


def test_a_truncated_antecedent_cannot_authorize_pronoun_resolution(env):
    store,service,_=env;session=store.create_session()['id']
    _,old=seed(store,service,'我维护星桥项目。'+'项目记录内容。'*100+'还有另一个项目。',session)
    linking(service,old['id'])
    _,job=send(store,service,'这个项目增加了回滚模块。',session)
    assert job['status']=='completed' and job['extracted_count']==0


def test_nli_cannot_merge_different_literal_project_names(env):
    store,service,_=env;_,old=seed(store,service)
    linking(service,old['id'])
    current,job=send(store,service,'海岚项目增加了回滚模块。')
    assert job['status']=='completed' and job['extracted_count']==1
    row=store.db.execute('SELECT id FROM memory_observations WHERE message_id=?',(current['id'],)).fetchone()
    assert history.load(store,row['id'])['proposal']['subject_ref']=='unknown'
    new=next(m for m in store.memories() if m['source_ref']==current['id'])
    assert new['content']=='海岚项目增加了回滚模块。' and store.memory(old['id'])['status']=='active'


@pytest.mark.parametrize('association_status',['Supported','Contradicted'])
@pytest.mark.parametrize('admission',['accept','reject'])
def test_history_audit_cannot_erase_a_literal_conflict(env,association_status,admission):
    from memoria.memory.access import QueryRequest
    store,service,_=env
    first='我通常用中文交流。'
    def typed(stage,output,data):
        if stage=='review':output['claim']={'predicate':'response_language','value':'中文','scope':{}}
    model(service,specs={first:observer(first,content=first,category='habit',asserted=True,durable=True)},mutate=typed)
    source,job=send(store,service,first)
    old=next(m for m in store.memories() if m['source_ref']==source['id'])
    linking(service,old['id'],status=association_status,relation='revision_candidate')
    original=service.llm.chat
    async def wrapped(messages,**kwargs):
        result=await original(messages,**kwargs)
        if '你是长期记忆的证据审核员' in messages[0]['content']:
            data=json.loads(result.content);data['claim']={'predicate':'response_language','value':'英文','scope':{}}
            data['decision']=admission;data['assessment']='Supported' if admission=='accept' else 'Contradicted'
            result.content=json.dumps(data,ensure_ascii=False)
        return result
    service.llm.chat=wrapped
    current,job=send(store,service,'我通常用英文交流。')
    assert job['status']=='completed' and job['extracted_count']==1
    access=asyncio.run(service.kernel.access(QueryRequest(query='我的回答语言是什么？')))
    assert access['path']=='clarify' and access['pending_revisions'][0]['source_ref']==current['id']
    assert store.memory(old['id'])['status']=='active'


@pytest.mark.parametrize('relation',['new_event','revision_candidate','elaboration','duplicate'])
def test_optional_relation_without_target_preserves_independent_current_fact(env,relation):
    store,service,client=env
    text='我维护海岚项目。'
    model(service,specs={text:observer(text,content=text,category='fact',asserted=True,durable=True,
        relation=relation,subject_ref='self')})
    source,job=send(store,service,text)
    assert job['status']=='completed' and job['extracted_count']==1 and len(store.memories())==1
    row=next(r for r in client.get('/api/memory-observations').json() if r['message_id']==source['id'])
    assert row['history']['relation']=='unknown' and row['history']['linked_memory_ids']==[]
