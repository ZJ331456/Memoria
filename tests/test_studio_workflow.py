"""Temporary engineering accounts only; no model calls or personal imports."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from memoria.api import create_app
from memoria.memory.worker import MemoryJobWorker


@pytest.fixture
def env(tmp_path, monkeypatch):
    async def idle(_): await asyncio.Event().wait()
    monkeypatch.setattr(MemoryJobWorker,'run',idle)
    config=tmp_path/'config.toml'
    config.write_text(f'[storage]\ndatabase="{(tmp_path/"data/main.db").as_posix()}"\n[memory.embedding]\nenabled=false\n')
    app=create_app(config)
    account=app.state.accounts.register('workflow','Temporary-workflow-password')
    with TestClient(app) as client:
        login=client.post('/api/auth/login',json={'username':'workflow','password':'Temporary-workflow-password'})
        assert login.status_code==200
        client.get('/api/studio/summary')
        store=app.state.tenants.apps[account['id']].state.store
        sid=store.create_session('Temporary engineering source')['id']
        message=store.add_message(sid,'user','我喜欢用中文回答。')
        yield app,client,store,message


def test_manual_source_proposal_is_idempotent_pending_and_approval_is_explicit(env):
    app,client,store,message=env
    body={'message_id':message['id'],'content':'用户喜欢用中文回答','kind':'preference','importance':3}
    proposed=client.post('/api/memory-reviews/propose',json=body)
    assert proposed.status_code==201
    row=proposed.json();assert row['status']=='pending' and row['job_id'].startswith('manual:')
    assert client.post('/api/memory-reviews/propose',json=body).json()['id']==row['id']
    assert store.db.execute('select count(*) from memory_jobs').fetchone()[0]==0
    assert store.db.execute('select count(*) from memories').fetchone()[0]==0
    approved=client.post('/api/memory-reviews/'+row['id']+'/approve',json={k:body[k] for k in ['content','kind','importance']})
    assert approved.status_code==200 and approved.json()['status']=='approved'
    assert store.db.execute('select count(*) from memories').fetchone()[0]==1
    assert store.db.execute('select count(*) from claim_roots').fetchone()[0]==0
    state=client.get('/api/studio/pipeline').json()
    assert state['counts']['memories']==1 and state['counts']['reviews']['approved']==1
    assert state['model_called'] is False


def test_public_assistant_missing_and_revoked_sources_cannot_propose(env):
    _,client,store,message=env
    assistant=store.add_message(message['session_id'],'assistant','Temporary assistant statement')
    def post(mid):return client.post('/api/memory-reviews/propose',json={'message_id':mid,'content':'候选内容'})
    assert post(assistant['id']).status_code in (409,422)
    assert post('f'*32).status_code==409
    receipt={'source_mapping':[{'source_id':'engineering-public','session_id':message['session_id'],'message_ids':[message['id'],assistant['id']]}]}
    with store.lock,store.db:
        store.db.execute('INSERT INTO dataset_imports VALUES(?,?,?,?,?)',
            ('temporary-public','engineering-source-sha','{}',json.dumps(receipt),message['created_at']))
    assert post(message['id']).status_code in (409,422)
    assert store.db.execute('select count(*) from memory_reviews').fetchone()[0]==0


def test_revocation_between_proposal_and_approval_keeps_review_pending(env):
    _,client,store,message=env
    body={'message_id':message['id'],'content':'用户喜欢用中文回答','kind':'preference','importance':3}
    row=client.post('/api/memory-reviews/propose',json=body).json()
    store.undo_memory_sources([message['id']])
    assert client.post('/api/memory-reviews/propose',json=body).status_code in (409,422)
    result=client.post('/api/memory-reviews/'+row['id']+'/approve',json={k:body[k] for k in ['content','kind','importance']})
    assert result.status_code in (409,422)
    assert store.memory_review(row['id'])['status']=='pending'
    assert store.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0]==0


def test_lost_lease_cannot_publish_extraction_counts(env):
    _,_,store,message=env
    job=store.enqueue_memory_job(message['id'],message['content'],'Temporary reply')
    store.claim_memory_job('current-worker')
    with pytest.raises(RuntimeError,match='lease lost'):
        store.stage_memory_reviews(job['id'],'stale-worker',[])
    persisted=store.memory_job(job['id'])
    assert persisted['extracted_count'] is None and persisted['staged_count'] is None


def test_pipeline_does_not_count_deleted_import_sessions(env):
    _,client,store,message=env
    with store.lock,store.db:
        store.db.execute('INSERT INTO dataset_imports VALUES(?,?,?,?,?)',('stale-import','sha','{}',
            json.dumps({'source_mapping':[{'session_id':'deleted-session','message_ids':[]}]}),message['created_at']))
    counts=client.get('/api/studio/pipeline').json()['counts']
    assert counts['imported_conversations']==0 and counts['native_conversations']==1


def test_pipeline_distinguishes_known_empty_from_legacy_unknown_without_raw_text(env):
    _,client,store,message=env
    job=store.enqueue_memory_job(message['id'],message['content'],'Temporary reply')
    leased=store.claim_memory_job('engineering-worker')
    assert leased['id']==job['id']
    store.stage_memory_reviews(job['id'],'engineering-worker',[])
    store.finish_memory_job(job['id'],owner='engineering-worker')
    state=client.get('/api/studio/pipeline')
    assert state.status_code==200
    data=state.json()
    assert data['extraction']['known_empty']==1
    assert data['extraction']['legacy_outcome_unknown']==0
    assert data['extraction']['recent'][0]['extracted_count']==0
    assert data['extraction']['recent'][0]['review_count']==0
    assert message['content'] not in state.text and 'api_key' not in state.text
    with store.lock,store.db:store.db.execute('update memory_jobs set extracted_count=NULL,staged_count=NULL')
    data=client.get('/api/studio/pipeline').json()
    assert data['extraction']['legacy_outcome_unknown']==1 and data['extraction']['known_empty']==0


def test_pipeline_and_manual_sources_follow_the_signed_in_owner(env):
    app,client,store,message=env
    other=app.state.accounts.register('another','Temporary-workflow-password')
    client.post('/api/auth/logout')
    client.post('/api/auth/login',json={'username':'another','password':'Temporary-workflow-password'})
    state=client.get('/api/studio/pipeline')
    assert state.status_code==200 and state.json()['counts']['sessions']==0
    assert client.post('/api/memory-reviews/propose',json={'message_id':message['id'],'content':'Should not enter another account'}).status_code==409
    assert client.post('/api/messages/'+message['id']+'/memory-extraction',json={}).status_code==409
    assert client.post('/api/memory-reviews/nonexistent/auto-review').status_code==404
    assert message['id'] not in state.text
    client.post('/api/auth/logout')
    assert client.get('/api/studio/pipeline').status_code==401
