"""Temporary account fixtures; never production users or research histories."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from memoria.accounts import AccountStore
from memoria.api import create_app


@pytest.fixture
def app(tmp_path):
    config=tmp_path/'config.toml'
    config.write_text(f'[storage]\ndatabase="{(tmp_path/"legacy.db").as_posix()}"\n[agent.mcp]\nenabled=false\n[memory.markdown]\nenabled=false\n')
    return create_app(config)


def login(client, username, password='Engineering-roles-password'):
    result=client.post('/api/auth/login',json={'username':username,'password':password})
    assert result.status_code==200
    return result.json()


def test_existing_schema_migration_preserves_identity_and_password(tmp_path):
    from memoria.accounts import password_hash
    path=tmp_path/'accounts.db'
    salt=b'engineering-salt'
    hashed=password_hash('Engineering-original-password',salt)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE users(id TEXT PRIMARY KEY,username TEXT NOT NULL UNIQUE,salt BLOB,password_hash BLOB,created_at REAL NOT NULL)')
        db.execute('INSERT INTO users VALUES(?,?,?,?,?)',('existing-fixture-id','qin',salt,hashed,1.0))
    store=AccountStore(path)
    try:
        user,token=store.login('qin','Engineering-original-password')
        assert user=={'id':'existing-fixture-id','username':'qin','role':'user'}
        assert store.authenticate(token)==user
        assert store.db.execute('SELECT password_hash FROM users').fetchone()[0]==hashed
    finally:store.close()


def test_registration_cannot_grant_admin_and_ordinary_users_are_denied(app):
    with TestClient(app) as client:
        assert client.get('/api/admin/users').status_code==401
        assert client.post('/api/auth/register',json={'username':'qin','password':'Engineering-roles-password','role':'superadmin'}).status_code==422
        assert client.post('/api/auth/register',json={'username':'admin','password':'Engineering-roles-password'}).json()['role']=='user'
        user=login(client,'admin')['user']
        assert user['role']=='user'
        assert client.get('/api/admin/users').status_code==403
        assert client.put('/api/admin/users/admin/password',json={'password':'Another-engineering-password'}).status_code==403
        assert client.post('/api/auth/register',json={'username':'short','password':'short!'}).status_code==422


def test_superadmin_metadata_password_reset_and_current_tenant_isolation(app):
    accounts=app.state.accounts
    original=accounts.register('qin','Engineering-roles-password')
    other=accounts.register('alice','Engineering-roles-password')
    accounts.set_role('qin','superadmin')
    assert accounts.user('qin')['id']==original['id']
    alice_token=accounts.login('alice','Engineering-roles-password')[1]
    with TestClient(app) as client:
        identity=login(client,'qin')
        assert identity['user']['role']=='superadmin'
        users=client.get('/api/admin/users')
        assert users.status_code==200 and {u['username'] for u in users.json()}=={'qin','alice'}
        assert 'password_hash' not in users.text and 'salt' not in users.text
        # Administrative rights do not change which tenant a normal data request uses.
        sessions=client.get('/api/sessions')
        assert sessions.status_code==200 and sessions.json()==[]
        assert set(app.state.tenants.apps)=={original['id']}
        assert other['id'] not in app.state.tenants.apps
        reset=client.put('/api/admin/users/alice/password',json={'password':'New-engineering-password'})
        assert reset.status_code==204 and not reset.content
        assert accounts.authenticate(alice_token) is None
        assert accounts.login('alice','Engineering-roles-password') is None
        assert accounts.login('alice','New-engineering-password')[0]['id']==other['id']
        event=accounts.db.execute("SELECT * FROM account_events WHERE action='superadmin_password_reset'").fetchone()
        assert event['actor_user_id']==original['id'] and event['target_user_id']==other['id']
        assert 'New-engineering-password' not in str(dict(event))


def test_role_changes_revoke_sessions_and_last_superadmin_is_protected(tmp_path):
    accounts=AccountStore(tmp_path/'accounts.db')
    try:
        accounts.register('qin','Engineering-roles-password')
        accounts.register('alice','Engineering-roles-password')
        old=accounts.login('qin','Engineering-roles-password')[1]
        accounts.set_role('qin','superadmin')
        assert accounts.authenticate(old) is None
        with pytest.raises(ValueError,match='最后一个'):accounts.set_role('qin','user')
        with pytest.raises(ValueError):accounts.disable_login('qin')
        accounts.set_role('alice','superadmin')
        token=accounts.login('qin','Engineering-roles-password')[1]
        accounts.set_role('qin','user')
        assert accounts.authenticate(token) is None
        with pytest.raises(PermissionError):
            accounts.reset_password_as_superadmin(token,'alice','Another-engineering-password')
        accounts.disable_login('qin')
        assert accounts.login('qin','Engineering-roles-password') is None
        assert accounts.user('qin') is not None
        assert accounts.user('alice')['role']=='superadmin'
    finally:accounts.close()
