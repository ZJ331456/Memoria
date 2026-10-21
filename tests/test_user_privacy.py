import hashlib
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from memoria.api import create_app
from memoria.accounts import user_directory
from memoria.private_files import save_private_json, load_private_json


@pytest.fixture
def private_api(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(f'[storage]\ndatabase="{(tmp_path / "legacy.db").as_posix()}"\n'
                      '[agent.mcp]\nenabled=false\n[memory.markdown]\nenabled=true\n')
    app = create_app(config)
    with TestClient(app) as client:
        yield app, client, tmp_path


def signup(client, username):
    body = {"username": username, "password": "Engineering-passphrase-2026"}
    assert client.post("/api/auth/register", json=body).status_code == 201
    response = client.post("/api/auth/login", json=body)
    assert response.status_code == 200
    return response.json()["user"], {"Authorization": "Bearer " + response.json()["access_token"]}


def test_registration_login_revocation_expiry_and_no_secret_echo(private_api):
    app, client, root = private_api
    assert client.get('/api/memories').status_code == 401
    assert client.get('/metrics').status_code == 401
    assert client.get('/api/memories',headers={'X-API-Key':'old-admin','X-Agent-Key':'key'}).status_code == 401
    response=client.post('/api/auth/register',json={'username':'qin','password':'too-short-private-password','user_id':'forged'})
    assert response.status_code==422 and 'too-short-private-password' not in response.text
    user,headers=signup(client,'qin')
    assert client.get('/api/auth/me',headers=headers).json()==user
    cookie=client.cookies.get('memoria_session')
    assert cookie and 'HttpOnly' in client.post('/api/auth/login',json={'username':'qin','password':'Engineering-passphrase-2026'}).headers['set-cookie']
    hashed=app.state.accounts.db.execute('SELECT password_hash FROM users WHERE id=?',(user['id'],)).fetchone()[0]
    assert b'Engineering-passphrase' not in hashed
    assert client.post('/api/auth/logout',headers=headers).status_code==204
    assert client.get('/api/auth/me',headers=headers).status_code==401
    assert client.post('/api/auth/login',json={'username':'qin','password':'A-wrong-password-2026'}).status_code==401
    assert client.get('/openapi.json').json()['components']['securitySchemes']['UserSession']['scheme']=='bearer'
    assert not (root/'legacy.db').exists()


def test_all_personal_routes_and_tools_are_owned_by_login_user(private_api):
    app,client,root=private_api
    qin,q=signup(client,'qin')
    alice,a=signup(client,'alice')
    sid=client.post('/api/sessions',json={'title':'qin confidential'},headers=q).json()['id']
    m=client.post('/api/memories',json={'content':'qin 私密记忆：喜欢研究天文','kind':'preference'},headers=q)
    assert m.status_code==200,m.text
    mid=m.json()['memory']['id']
    assert client.get('/api/memories',headers=a).json()==[]
    assert client.get('/api/sessions',headers=a).json()==[]
    assert 'qin confidential' not in client.get('/api/overview',headers=a).text
    assert client.get('/api/sessions/'+sid,headers=a).status_code==404
    assert client.get('/api/sessions/'+sid+'/messages',headers=a).status_code==404
    assert client.delete('/api/sessions/'+sid,headers=a).status_code==404
    for suffix in ['', '/history','/timeline','/neighbors']:
        assert client.get('/api/memories/'+mid+suffix,headers=a).status_code==404
    assert client.delete('/api/memories/'+mid,headers=a).status_code==404
    assert client.patch('/api/memories/'+mid,json={'content':'hijacked'},headers=a).status_code==404
    exported = client.get('/api/memories/export.md',headers=q)
    assert exported.status_code == 200 and 'qin 私密记忆' in exported.text
    assert 'qin 私密记忆' not in client.get('/api/memories/export.md',headers=a).text
    assert 'qin 私密记忆' not in client.get('/api/memories',params={'query':'天文','user_id':qin['id']},headers=a).text
    assert 'qin 私密记忆' not in client.post('/api/tools/recall/execute',json={'arguments':{'query':'天文'}},headers=a).text
    assert client.put('/api/markdown/self',json={'content':'qin-only profile'},headers=q).status_code==200
    assert 'qin-only profile' not in client.get('/api/markdown/self',headers=a).text
    qa=app.state.tenants.apps[qin['id']]
    aa=app.state.tenants.apps[alice['id']]
    assert qa.state.store is not aa.state.store
    assert qa.state.service.runtime.memory is not aa.state.service.runtime.memory
    assert qa.state.settings.database!=aa.state.settings.database
    assert qa.state.settings.markdown_directory!=aa.state.settings.markdown_directory

    source=qa.state.store.add_message(sid,'user','Private source text')
    assert client.get('/api/messages/'+source['id']+'/source',headers=a).status_code==404
    assert client.get('/api/messages/'+source['id']+'/source',headers=q).status_code==200
    assert qa.state.service.mcp.enabled is False
    assert aa.state.service.mcp.enabled is False
    assert client.get('/api/memory-jobs',headers=a).json()==[]
    assert client.get('/api/memory-reviews',headers=a).json()==[]
    assert client.get('/api/traces',headers=a).json()==[]
    assert client.get('/api/episodes',headers=a).json()==[]
    assert client.get('/api/memories/'+mid,headers=q).status_code==200
    assert client.put('/api/settings/models',json={'main': {'base_url': 'https://attacker.invalid/v1'}},headers=a).status_code == 403
    assert aa.state.settings.main.base_url == qa.state.settings.main.base_url
    with qa.state.store.db:
        qa.state.store.db.execute('INSERT INTO dataset_imports VALUES(?,?,?,?,?)', ('engineering-fixture', 'sha', '{}', '{"source_mapping":[]}', 'now'))
    assert len(client.get('/api/datasets',headers=q).json()) == 1
    assert client.get('/api/datasets',headers=a).json() == []
    assert client.get('/api/datasets/engineering-fixture',headers=a).status_code == 404




def test_formation_read_routes_do_not_cross_accounts_or_call_models(private_api):
    from memoria.memory.source_support import digest
    app,client,_=private_api
    alice,a=signup(client,'alice')
    bob,b=signup(client,'bob')
    for headers in (a,b):
        assert client.get('/api/memory-formation',headers=headers).status_code==200
    tenant=app.state.tenants.apps[alice['id']]
    store=tenant.state.store
    source=store.add_message(store.create_session()['id'],'user','engineering observation private to alice')
    with store.db:
        store.db.execute('INSERT INTO memory_formation_runs VALUES(?,?,?,?,?,?,?)',
            ('engineering-run','engineering-job',digest(source['content']),'engineering-only','no-model',None,source['created_at']))
        store.db.execute("INSERT INTO memory_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active')",
            ('engineering-observation','engineering-run',source['id'],digest(source['content']),source['session_id'],source['created_at'],
             'interest','engineering-topic','','positive',source['content'],source['content'],0,len(source['content']),0,0,3))
    calls=[]
    async def forbidden(*args,**kwargs):calls.append(True);raise AssertionError('Read routes cannot call models')
    tenant.state.service.llm.chat=forbidden
    assert len(client.get('/api/memory-observations',headers=a).json())==1
    assert client.get('/api/memory-observations',headers=b,params={'user_id':alice['id']}).json()==[]
    assert client.get('/api/memory-patterns',headers=b).json()==[]
    assert client.get('/api/memory-formation',headers=b).json()['observations']=={}
    client.cookies.clear()
    for route in ('memory-formation','memory-observations','memory-patterns'):
        assert client.get('/api/'+route).status_code==401
    assert calls==[]

def test_reserved_user_cannot_be_claimed_and_password_reset_revokes_sessions(private_api):
    app,client,root=private_api
    user=app.state.accounts.reserve('qin')
    body={'username':'qin','password':'Engineering-passphrase-2026'}
    assert client.post('/api/auth/register',json=body).status_code==409
    assert client.post('/api/auth/login',json=body).status_code==401
    app.state.accounts.set_password('qin',body['password'])
    token=client.post('/api/auth/login',json=body).json()['access_token']
    app.state.accounts.set_password('qin','Another-private-passphrase-2026')
    assert client.get('/api/auth/me',headers={'Authorization':'Bearer '+token}).status_code==401


def test_cookie_csrf_and_login_rate_limit(private_api):
    app,client,root=private_api
    user,h=signup(client,'qin')
    assert client.post('/api/sessions',json={},headers={'Origin':'https://attacker.invalid'}).status_code==403
    assert client.post('/api/sessions',json={},headers={'Sec-Fetch-Site':'cross-site'}).status_code==403
    for _ in range(10):
        app.state.accounts.throttle('name:blocked')
    assert client.post('/api/auth/login',json={'username':'blocked','password':'A-wrong-password-2026'}).status_code==429
    token=h['Authorization'][7:]
    with app.state.accounts.db:
        app.state.accounts.db.execute('UPDATE login_sessions SET expires_at=? WHERE token_hash=?',(time.time()-1,hashlib.sha256(token.encode()).hexdigest()))
    assert client.get('/api/memories',headers=h).status_code==401


def test_streaming_body_cap_and_remote_tls_boundary(private_api):
    app, client, root = private_api
    response = client.post('/api/auth/login', content=iter([b'x' * 600000, b'y' * 600000]), headers={'Content-Type': 'application/json'})
    assert response.status_code == 413
    with TestClient(app, client=('203.0.113.1', 12345)) as remote:
        assert remote.post('/api/auth/login', json={'username': 'qin', 'password': 'Engineering-passphrase-2026'}).status_code == 426
        assert remote.get('/api/health').status_code == 200
        assert remote.get('/metrics').status_code == 426


def test_storage_path_rejects_client_chosen_ids(tmp_path):
    for name in ['qin','../other','a'*31,'../../data/memoria.db']:
        with pytest.raises(ValueError):user_directory(tmp_path,name)


def test_password_reset_during_login_cannot_create_an_old_password_session(private_api, monkeypatch):
    import memoria.persistence.accounts as accounts_module
    app, client, root = private_api
    app.state.accounts.register('qin', 'Engineering-passphrase-2026')
    original = accounts_module.password_hash
    pending = True

    def concurrent_reset(password, salt):
        nonlocal pending
        computed = original(password, salt)
        if pending:
            pending = False
            app.state.accounts.set_password('qin', 'New-private-passphrase-2026')
        return computed

    monkeypatch.setattr(accounts_module, 'password_hash', concurrent_reset)
    assert app.state.accounts.login('qin', 'Engineering-passphrase-2026') is None
    assert app.state.accounts.db.execute('SELECT COUNT(*) FROM login_sessions').fetchone()[0] == 0


@pytest.mark.skipif(os.name!='nt',reason='Windows user-bound DPAPI')
def test_credentials_are_encrypted_at_rest(tmp_path):
    path=tmp_path/'credentials.dpapi'
    value={'app_id':'test-application','app_secret':'Engineering-only-test-value'}
    save_private_json(path,value)
    assert load_private_json(path)==value
    assert value['app_secret'].encode() not in path.read_bytes()
