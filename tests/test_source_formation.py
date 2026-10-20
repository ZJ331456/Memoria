"""Isolated protocol/transaction tests, not a longitudinal or extraction benchmark."""
import asyncio
import json
from dataclasses import replace

import pytest

from memoria.api.app import _create_tenant_app
from memoria.llm.types import ChatResult
from memoria.memory.library import SourceInput, FeedbackInput, RevisionInput, LibraryError
from memoria.memory.records import MemorySearch
from memoria.memory.source_formation import FormRequest, OBSERVE, VERIFY, CONSOLIDATE, CHECK_CONSOLIDATION
from tests.test_memory_kernel import config_at


@pytest.fixture
def env(tmp_path):
    app=_create_tenant_app(config_at(tmp_path))
    service=app.state.service
    yield service,service.library,service.library.formation
    app.state.store.close()


def model(service, *, alter=None, action=None):
    calls=[]
    async def chat(messages,**kwargs):
        prompt=messages[0]['content']; data=json.loads(messages[1]['content']);calls.append(prompt)
        if prompt==OBSERVE:
            quote=data['source']
            output={'observations':[{'category':'knowledge','topic':'部署校验','subject':'Memoria','conditions':'',
                'polarity':'positive','epistemic':'asserted','content':'据所给资料，Memoria部署前要核对配置。',
                'quote':quote,'feedback_quote':data['feedback']['content'] if data['feedback'] else '',
                'applicability':'仅限所给资料中的Memoria部署，未验证普适性'}]}
        elif prompt==VERIFY:
            output={'verdicts':[{'id':c['id'],'assessment':'Supported','attribution_preserved':True,
                'conditions_preserved':True,'task_bound':True,'sensitive':False} for c in data['candidates']]}
        elif prompt==CONSOLIDATE:
            previous=data['existing']
            output={'action':action or ('REINFORCE' if previous else 'ADD'),
                'content':previous['content'] if previous else '据这些资料，Memoria部署流程包含配置核对。',
                'applicability':previous['applicability'] if previous else '仅限这些Memoria部署资料，不代表其他项目',
                'observation_ids':[o['id'] for o in data['observations']], 'reason':'原样证据支持有限归纳'}
        elif prompt==CHECK_CONSOLIDATION:
            output={'assessment':'Supported','attribution_preserved':True,'conditions_preserved':True,
                    'task_bound':True,'sensitive':False}
        else:raise AssertionError('unexpected paid-model stage')
        if alter:alter(prompt,data,output,len(calls))
        return ChatResult(content=json.dumps(output,ensure_ascii=False),raw_message={},usage={'total_tokens':21})
    service.llm.chat=chat
    return calls


def add(library, number, **kwargs):
    fields={'kind':'document','title':f'工程来源{number}','content':f'Memoria部署需要核对配置。来源记录{number}。','authorship':'external'}
    fields.update(kwargs)
    return library.create(SourceInput(**fields))


def form(formation, item, **kwargs):
    return asyncio.run(formation.form(item['id'],FormRequest(**kwargs)))


def ready(env):
    service,library,formation=env
    calls=model(service)
    sources=[add(library,i) for i in range(3)]
    receipts=[form(formation,s) for s in sources]
    return calls,sources,receipts[-1]['consolidation']


def test_source_aware_formation_requires_multiple_distinct_sources_and_independent_review(env):
    service,library,formation=env;calls=model(service)
    a=add(library,1);first=form(formation,a)
    assert first['source_complete'] and first['observations'][0]['lane']=='source_knowledge'
    assert first['consolidation']['status']=='insufficient_sources' and len(calls)==2
    form(formation,add(library,2));third=form(formation,add(library,3))
    assert third['consolidation']['status']=='approved' and len(calls)==8
    assert service.store.memories()==[] and service.store.sessions()==[]
    assert service.store.db.execute('SELECT count(*) FROM claim_roots').fetchone()[0]==0
    assert service.store.db.execute('SELECT count(*) FROM library_observations').fetchone()[0]==3


def test_cached_form_and_no_new_evidence_do_not_repeat_paid_calls(env):
    service,library,formation=env;calls,sources,_=ready(env)
    receipt=form(formation,sources[-1])
    assert receipt['cached'] and receipt['consolidation']['status']=='no_new_evidence' and len(calls)==8
    assert sum(json.loads(r[0])['total_tokens'] for r in service.store.db.execute('SELECT usage_json FROM library_calls'))==168


def test_disabled_formation_also_blocks_explicit_consolidation_without_model_calls(env,monkeypatch):
    service,_,formation=env;ready(env)
    oid=service.store.db.execute('SELECT id FROM library_observations LIMIT 1').fetchone()[0]
    calls=model(service)
    monkeypatch.setattr(service.settings,'memory_formation',replace(service.settings.memory_formation,enabled=False))
    with pytest.raises(LibraryError,match='关闭'):asyncio.run(formation.consolidate(oid))
    assert calls==[]


def test_manual_policy_cannot_be_bypassed_by_explicit_consolidation(env,monkeypatch):
    service,library,formation=env;calls=model(service)
    original=formation.consolidate
    async def deferred(_):return {'status':'deferred_engineering_fixture'}
    monkeypatch.setattr(formation,'consolidate',deferred)
    for i in range(3):receipt=form(formation,add(library,i))
    monkeypatch.setattr(formation,'consolidate',original)
    monkeypatch.setattr(service.settings,'memory_approval_mode','manual')
    result=asyncio.run(formation.consolidate(receipt['observations'][0]['id']))
    assert result['status']=='needs_assistance' and len(calls)==8
    assert formation.search('Memoria')==[]
    assert formation.list()[0]['versions'][0]['reason']=='manual_policy'


def test_copied_bytes_do_not_increase_distinct_support_count(env):
    service,library,formation=env;calls=model(service)
    for i in range(3):
        result=form(formation,add(library,i,content='Memoria部署核对同一份配置说明。'))
    assert result['consolidation']['status']=='insufficient_sources'
    assert result['consolidation']['distinct_sources']==1 and len(calls)==6


def test_external_first_person_text_cannot_become_a_personal_observation(env):
    service,library,formation=env
    def alter(stage,data,output,_):
        if stage==OBSERVE:output['observations'][0]['subject']='self'
    model(service,alter=alter)
    item=add(library,1,content='我在Memoria部署前核对配置。')
    receipt=form(formation,item)
    assert receipt['observations'][0]['status']=='needs_assistance'
    assert service.store.memories()==[]


def test_self_note_can_form_a_bounded_personal_observation_without_making_claim(env):
    service,library,formation=env
    def alter(stage,data,output,_):
        if stage==OBSERVE:
            output['observations'][0].update(subject='self',category='workflow',conditions='发布前')
    model(service,alter=alter)
    receipt=form(formation,add(library,1,kind='note',authorship='self',content='我在Memoria发布前核对配置。'))
    assert receipt['observations'][0]['lane']=='personal_observation'
    assert receipt['observations'][0]['status']=='approved'
    assert service.store.db.execute('SELECT count(*) FROM claim_roots').fetchone()[0]==0


@pytest.mark.parametrize('fault',['missing_verdict','fabricated_quote','external_entity','sensitive','unsupported'])
def test_incomplete_or_unsupported_observations_are_not_usable(env,fault):
    service,library,formation=env
    def alter(stage,data,output,_):
        if stage==OBSERVE and fault=='fabricated_quote':output['observations'][0]['quote']='not-in-source'
        if stage==OBSERVE and fault=='external_entity':output['observations'][0]['subject']='Unmentioned'
        if stage==VERIFY and fault=='missing_verdict':output['verdicts']=[]
        if stage==VERIFY and fault=='sensitive':output['verdicts'][0]['sensitive']=True
        if stage==VERIFY and fault=='unsupported':output['verdicts'][0]['assessment']='Insufficient'
    model(service,alter=alter);item=add(library,1)
    if fault in {'fabricated_quote','missing_verdict'}:
        with pytest.raises(LibraryError):form(formation,item)
        assert library.detail(item['id'])['cards']==[]
    else:
        result=form(formation,item)
        assert result['observations'][0]['status']=='needs_assistance'
        assert library.search('Memoria')[0]['knowledge_cards']==[]


def test_task_experience_requires_feedback_and_unknown_outcome_is_not_success(env):
    service,library,formation=env;calls=model(service)
    item=add(library,1,kind='task_result')
    with pytest.raises(LibraryError):form(formation,item)
    assert calls==[]
    library.feedback(item['id'],FeedbackInput(expected_revision_id=item['revision']['id'],outcome='unknown',content='工程任务尚未验收。'))
    result=form(formation,item)
    assert result['observations'][0]['lane']=='task_experience'
    assert result['consolidation']['status']=='unknown_outcome'


def test_partial_processing_is_explicit_and_cannot_consolidate_incomplete_source(env):
    service,library,formation=env;calls=model(service)
    item=add(library,1,content='Memoria部署需要检查甲。\nMemoria部署需要检查乙。')
    first=form(formation,item,max_chars=16)
    assert not first['source_complete'] and first['processed_span']['end']==16
    assert first['consolidation']['status']=='incomplete_source'
    second=form(formation,item,start=16)
    assert second['source_complete']
    assert len(calls)==4


def test_version_retraction_and_feedback_dependencies_stop_old_consolidation(env):
    service,library,formation=env;_,sources,consolidation=ready(env)
    current=formation.read(consolidation['root_id'])
    assert formation.dependency_valid(current['dependency'])
    hit=asyncio.run(service.kernel.search(MemorySearch(query='Memoria部署',domains=['library'],memory_mode='bm25')))
    assert any(e['representation']=='consolidated_memory' for e in hit['evidence'])
    assert service.kernel.read('library',current['source_id'])['evidence']['representation']=='consolidated_memory'
    library.retract(sources[0]['id'])
    assert not formation.dependency_valid(current['dependency'])
    assert formation.search('Memoria部署')==[]
    assert formation.list()[0]['memory'] is None


def test_new_unreviewed_observation_suspends_old_synthesis_then_reinforces_with_revision(env):
    service,library,formation=env;_,_,head=ready(env)
    before=formation.read(head['root_id']);captured=[]
    def alter(stage,data,output,_):
        if stage==CONSOLIDATE:
            captured.append(formation.dependency_valid(before['dependency']))
    model(service,alter=alter)
    result=form(formation,add(library,4))
    assert captured==[False] and result['consolidation']['action']=='REINFORCE'
    assert not formation.dependency_valid(before['dependency'])
    assert formation.read(head['root_id'])['revision_id']!=head['revision_id']


def test_conflicting_polarity_is_reviewed_without_overwriting_the_head(env):
    service,library,formation=env;_,_,head=ready(env)
    def alter(stage,data,output,_):
        if stage==OBSERVE:output['observations'][0]['polarity']='negative'
    calls=model(service,alter=alter)
    result=form(formation,add(library,4))
    assert result['consolidation']['status']=='review_required' and len(calls)==2
    assert formation.search('Memoria')==[]
    assert service.store.db.execute('SELECT head_id FROM library_consolidations').fetchone()[0]==head['revision_id']


def test_retired_root_cannot_be_automatically_revived(env):
    service,library,formation=env;_,_,head=ready(env)
    formation.retire(head['root_id'],head['revision_id'])
    result=form(formation,add(library,4))
    assert result['consolidation']['status']=='retired' and formation.search('Memoria')==[]


def test_source_mutation_while_observation_model_waits_prevents_publication(env):
    service,library,formation=env;item=add(library,1)
    def alter(stage,data,output,_):
        if stage==VERIFY:library.retract(item['id'])
    model(service,alter=alter)
    with pytest.raises(LibraryError):form(formation,item)
    assert service.store.db.execute('SELECT count(*) FROM library_observations').fetchone()[0]==0


def test_source_mutation_while_consolidation_verifier_waits_prevents_publication(env):
    service,library,formation=env;model(service)
    sources=[add(library,i) for i in range(3)]
    form(formation,sources[0]);form(formation,sources[1])
    def alter(stage,data,output,_):
        if stage==CHECK_CONSOLIDATION:library.retract(sources[0]['id'])
    model(service,alter=alter)
    result=form(formation,sources[2])
    assert result['status']=='completed' and result['consolidation']['status']=='failed'
    assert result['observations'][0]['status']=='approved'
    assert formation.search('Memoria')==[]
    assert service.store.db.execute('SELECT count(*) FROM library_consolidation_versions').fetchone()[0]==0


def test_same_source_parallel_form_calls_are_coalesced(env):
    service,library,formation=env;calls=model(service);item=add(library,1)
    async def run():return await asyncio.gather(formation.form(item['id'],FormRequest()),formation.form(item['id'],FormRequest()))
    a,b=asyncio.run(run())
    assert a['observations']==b['observations'] and len(calls)==2


def test_update_uses_only_its_new_complete_group_and_revisions_are_immutable(env):
    import sqlite3
    service,library,formation=env;_,_,head=ready(env)
    def alter(stage,data,output,_):
        if stage==CONSOLIDATE:output['content']='据这些资料，Memoria部署需要新增的一次配置复核。'
    model(service,alter=alter,action='UPDATE')
    latest=add(library,4);result=form(formation,latest)
    assert result['consolidation']['status']=='approved'
    version=service.store.db.execute('SELECT * FROM library_consolidation_versions WHERE id=?',
        (result['consolidation']['revision_id'],)).fetchone()
    assert version['previous_id']==head['revision_id'] and len(json.loads(version['support_json']))==1
    with pytest.raises(sqlite3.IntegrityError):
        with service.store._memory_transaction():
            service.store.db.execute('UPDATE library_consolidation_versions SET content=? WHERE id=?',('changed',version['id']))
    library.retract(latest['id'])
    assert formation.search('Memoria')==[]  # Cannot borrow the previous version's three-source group.


def test_reinforce_can_keep_an_alternative_complete_group_after_source_withdrawal(env):
    service,library,formation=env;_,old_sources,head=ready(env)
    def alter(stage,data,output,_):
        if stage==CONSOLIDATE:output['observation_ids']=[o['id'] for o in data['observations'][:3]]
    model(service,alter=alter)
    for i in range(3,6):form(formation,add(library,i))
    for source in old_sources:library.retract(source['id'])
    current=formation.read(head['root_id'])
    assert len(current['support'])==3
    assert formation.dependency_valid(current['dependency'])


def test_condition_groups_do_not_merge_and_working_scope_is_preserved(env):
    service,library,formation=env;sid=service.store.create_session()['id']
    model(service)
    for i in range(3):result=form(formation,add(library,i,scope='working',session_id=sid))
    key=result['consolidation']['root_id']
    assert formation.search('Memoria')==[]
    assert formation.search('Memoria',session_id=sid)
    with pytest.raises(LibraryError):formation.read(key)
    assert service.kernel.read('library','consolidation:'+key,session_id=sid)
    other=service.store.create_session()['id']
    with pytest.raises(LibraryError):formation.read(key,session_id=other)
    assert formation.list(session_id=other)==[]
    service.store.delete_session(sid)
    assert formation.list(session_id=sid)==[]
    assert service.store.db.execute('SELECT count(*) FROM library_consolidation_versions').fetchone()[0]==0


def test_different_conditions_remain_separate_roots(env):
    service,library,formation=env
    def alter(stage,data,output,_):
        if stage==OBSERVE:output['observations'][0]['conditions']='开发环境' if '开发环境' in data['source'] else '生产环境'
    model(service,alter=alter)
    for i in range(6):
        condition='开发环境' if i<3 else '生产环境'
        form(formation,add(library,i,content=f'Memoria在{condition}核对配置。工程记录{i}。'))
    assert len(formation.search('Memoria'))==2


def test_external_fact_and_knowledge_labels_share_the_same_source_role(env):
    service,library,formation=env
    def alter(stage,data,output,_):
        if stage==OBSERVE:output['observations'][0]['category']='fact' if '记录1' in data['source'] else 'knowledge'
    model(service,alter=alter)
    for i in range(3):result=form(formation,add(library,i))
    assert result['consolidation']['status']=='approved'


def test_expiry_removes_derived_cards_but_keeps_original_documents(env,monkeypatch):
    service,library,formation=env;_,sources,_=ready(env)
    snapshot=library.search('Memoria')[0]['dependency']
    assert snapshot['card_ids'] and service.kernel.dependency_valid(snapshot)
    monkeypatch.setattr(formation,'_cutoff',lambda:'9999-01-01T00:00:00+00:00')
    assert not service.kernel.dependency_valid(snapshot)
    assert formation.search('Memoria')==[]
    assert all(not hit['knowledge_cards'] for hit in library.search('Memoria'))
    assert library.detail(sources[0]['id'])['revision']['content']


def test_noop_is_cached_and_never_claims_a_new_memory(env):
    service,library,formation=env;calls=model(service)
    def alter(stage,data,output,_):
        if stage==OBSERVE:output['observations']=[]
    calls=model(service,alter=alter);item=add(library,1)
    assert form(formation,item)['observations']==[]
    assert form(formation,item)['cached'] and len(calls)==1
    assert service.store.db.execute('SELECT count(*) FROM library_cards').fetchone()[0]==0


def test_partial_repeated_quote_binds_the_selected_global_span(env):
    service,library,formation=env;model(service)
    line='Memoria部署需要核对配置。'
    item=add(library,1,content=line+'\n'+line)
    result=form(formation,item,start=len(line)+1)
    card=library.detail(item['id'])['cards'][0]
    assert card['start']==len(line)+1 and card['quote']==line
    assert not result['source_complete']


def test_exact_root_heading_is_separate_subject_evidence_not_a_relaxed_value_quote(env):
    service,library,formation=env
    def alter(stage,data,output,_):
        if stage==OBSERVE:output['observations'][0]['quote']='部署前必须核对配置。'
    model(service,alter=alter)
    item=add(library,1,content='# Memoria\n部署前必须核对配置。\n```powershell\n# 命令注释不是另一个文档标题\n```')
    result=form(formation,item)
    assert result['observations'][0]['status']=='approved'
    observation=formation._observation(result['observations'][0]['id'])
    assert observation['subject_quote']=='# Memoria'
    assert item['revision']['content'][observation['subject_start']:observation['subject_end']]==observation['subject_quote']


def test_upload_filename_alone_cannot_bind_the_fact_subject(env):
    service,library,formation=env
    def alter(stage,data,output,_):
        if stage==OBSERVE:output['observations'][0]['quote']=data['source']
    model(service,alter=alter)
    item=add(library,1,title='Memoria部署文档',content='某个项目需要核对配置，但没有指明项目名称。')
    result=form(formation,item)
    assert result['observations'][0]['status']=='needs_assistance'


def test_explicit_subject_quote_is_literal_and_retained_in_support_group(env):
    service,library,formation=env
    def alter(stage,data,output,_):
        if stage==OBSERVE:
            output['observations'][0].update(quote='部署前必须核对配置。',subject_quote='此处说明Memoria。')
    model(service,alter=alter)
    for i in range(3):
        result=form(formation,add(library,i,content=f'工程记录{i}。此处说明Memoria。部署前必须核对配置。'))
    head=formation.read(result['consolidation']['root_id'])
    assert all(s['subject_quote']=='此处说明Memoria。' and s['subject_citation'] for s in head['support'])


def test_docx_title_style_can_bind_a_cross_paragraph_project_subject(env):
    from tests.test_document_import import docx
    service,library,formation=env
    def alter(stage,data,output,_):
        if stage==OBSERVE:output['observations'][0]['quote']='部署前必须核对配置。'
    model(service,alter=alter)
    raw=docx('<w:p><w:pPr><w:pStyle w:val="local-title"/></w:pPr><w:r><w:t>Memoria</w:t></w:r></w:p>'
             '<w:p><w:r><w:t>部署前必须核对配置。</w:t></w:r></w:p>',
             **{'word/styles.xml':'<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:style w:styleId="local-title"><w:name w:val="Title"/></w:style></w:styles>'})
    item=service.kernel.ingest_file('工程说明.docx',raw,authorship='external')
    result=form(formation,item)
    observation=formation._observation(result['observations'][0]['id'])
    assert observation['status']=='approved' and observation['subject_quote']=='Memoria'


def test_search_cards_keep_separate_subject_spans_and_recheck_approval_before_publication(env):
    service,library,formation=env
    def alter(stage,data,output,_):
        if stage==OBSERVE:output['observations'][0]['quote']='部署前必须核对配置。'
    model(service,alter=alter)
    item=add(library,1,content='# Memoria\n部署前必须核对配置。')
    form(formation,item)
    hit=library.search('部署')[0];card=hit['knowledge_cards'][0]
    assert card['quote']=='部署前必须核对配置。' and card['subject_quote']=='# Memoria'
    text=item['revision']['content']
    assert text[card['start']:card['end']]==card['quote']
    assert text[card['subject_start']:card['subject_end']]==card['subject_quote']
    assert card['citation'] and card['subject_citation'] and card['group']['subject']=='memoria'
    assert service.kernel.dependency_valid(hit['dependency'])
    with service.store._memory_transaction():
        service.store.db.execute("UPDATE library_cards SET status='needs_assistance' WHERE id=?",(card['id'],))
    assert not service.kernel.dependency_valid(hit['dependency'])


def test_windows_line_endings_are_losslessly_mapped_to_actual_evidence(env):
    service,library,formation=env
    def alter(stage,data,output,_):
        if stage==OBSERVE:output['observations'][0]['quote']=data['source'].replace('\r\n','\n')
    model(service,alter=alter)
    item=add(library,1,content='Memoria部署核对配置。\r\n保留工程证据。')
    form(formation,item)
    card=library.detail(item['id'])['cards'][0]
    assert card['quote']==item['revision']['content']
    assert json.loads(card['verdict_json'])['literal_normalizations']==['line_endings']


def test_feedback_changes_invalidate_task_observations_and_consolidation(env):
    service,library,formation=env;model(service);sources=[]
    for i in range(3):
        item=add(library,i,kind='task_result');sources.append(item)
        library.feedback(item['id'],FeedbackInput(expected_revision_id=item['revision']['id'],outcome='success',content=f'工程验收报告{i}：配置核对通过。'))
        result=form(formation,item)
    dependency=formation.read(result['consolidation']['root_id'])['dependency']
    library.feedback(sources[0]['id'],FeedbackInput(expected_revision_id=sources[0]['revision']['id'],outcome='failure',content='后来发现验收失败，撤回成功报告。'))
    assert not formation.dependency_valid(dependency)
    assert formation.search('Memoria')==[]
    assert library.search('Memoria')[0]['quote']  # Raw evidence survives; old experience does not.


def test_identical_feedback_retry_keeps_the_same_receipt_and_does_not_invalidate_experience(env):
    _,library,_=env
    item=add(library,1,kind='task_result')
    body=FeedbackInput(expected_revision_id=item['revision']['id'],outcome='success',content='工程验收通过。')
    a,b=library.feedback(item['id'],body),library.feedback(item['id'],body)
    assert a['id']==b['id']


def test_previous_feedback_coverage_cannot_complete_a_new_partial_attempt(env):
    service,library,formation=env;model(service)
    text='Memoria部署检查第一项。\nMemoria部署检查第二项。'
    item=add(library,1,kind='task_result',content=text)
    rid=item['revision']['id']
    library.feedback(item['id'],FeedbackInput(expected_revision_id=rid,outcome='success',content='工程验收通过。'))
    assert form(formation,item)['source_complete']
    library.feedback(item['id'],FeedbackInput(expected_revision_id=rid,outcome='failure',content='后续工程检查失败。'))
    result=form(formation,item,max_chars=16)
    assert not result['source_complete'] and result['consolidation']['status']=='incomplete_source'
    assert form(formation,item,start=16)['source_complete']


def test_stale_expected_revision_is_rejected_before_model_usage(env):
    service,library,formation=env;calls=model(service);item=add(library,1)
    library.revise(item['id'],RevisionInput(expected_revision_id=item['revision']['id'],title='新版',content='Memoria新配置。'))
    with pytest.raises(LibraryError):form(formation,item,expected_revision_id=item['revision']['id'])
    assert calls==[]


@pytest.mark.parametrize('kind',['length','provider_failure'])
def test_incomplete_and_failed_calls_preserve_unknown_usage_without_publishing(env,kind):
    service,library,formation=env
    async def bad(*args,**kwargs):
        if kind=='provider_failure':raise TimeoutError('engineering-only provider fault')
        return ChatResult(content='{"observations":[]}',raw_message={},finish_reason='length',usage={'total_tokens':9})
    service.llm.chat=bad
    with pytest.raises(LibraryError):form(formation,add(library,1))
    assert service.store.db.execute('SELECT count(*) FROM library_formation_runs').fetchone()[0]==0
    row=service.store.db.execute('SELECT * FROM library_calls').fetchone()
    assert row['status']=='provider_failed' if kind=='provider_failure' else row['status']=='incomplete_output'
    if kind=='provider_failure':assert row['usage_json'] is None


def test_full_evidence_budget_never_injects_half_a_consolidation(env):
    service,_,formation=env;ready(env)
    for budget in (800,2000,8000):
        result=asyncio.run(service.kernel.search(MemorySearch(query='Memoria部署',domains=['library'],budget_chars=budget)))
        assert len(json.dumps(result,ensure_ascii=False))==result['budget']['used']<=budget
        for evidence in result['evidence']:
            if evidence['representation']=='consolidated_memory':
                assert len(evidence['provenance']['support'])>=3
                assert all(e['quote'] and e['citation'] for e in evidence['provenance']['support'])


def test_file_and_formation_endpoints_use_the_authenticated_tenant(tmp_path,monkeypatch):
    from unittest.mock import AsyncMock
    from fastapi.testclient import TestClient
    from memoria.api import create_app
    from memoria.service import AgentService
    monkeypatch.setattr(AgentService,'start_integrations',AsyncMock())
    app=create_app(config_at(tmp_path))
    with TestClient(app) as client:
        def signup(name):
            body={'username':name,'password':'Engineering-only-passphrase-2026'}
            assert client.post('/api/auth/register',json=body).status_code==201
            login=client.post('/api/auth/login',json=body).json()
            return login['user']['id'],{'Authorization':'Bearer '+login['access_token']}
        owner,a=signup('source_a');_,b=signup('source_b')
        created=client.post('/api/library/import',headers=a,params={'filename':'工程1.txt'},content=b'Memoria deployment 1').json()
        service=app.state.tenants.apps[owner].state.service;calls=model(service)
        result=client.post('/api/library/sources/'+created['id']+'/form',headers=a,json={})
        assert result.status_code==200 and len(calls)==2
        oid=result.json()['observations'][0]['id']
        for endpoint in ('/api/library/sources/'+created['id']+'/file',
                         '/api/library/sources/'+created['id']+'/observations'):
            assert client.get(endpoint,headers=b).status_code==404
        assert client.post('/api/library/sources/'+created['id']+'/form',headers=b,json={}).status_code==404
        assert client.post('/api/library/observations/'+oid+'/consolidate',headers=b,json={}).status_code==404
        assert client.get('/api/library/consolidations',headers=b).json()==[]
        assert client.post('/api/library/sources/'+created['id']+'/form',headers=a,json={'user_id':'forged'}).status_code==422
        client.cookies.clear()
        assert client.get('/api/library/sources/'+created['id']+'/file').status_code==401
        assert client.post('/api/library/sources/'+created['id']+'/form',json={}).status_code==401
