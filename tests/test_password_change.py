"""Password changes in isolated engineering accounts, never a real user's secret."""
import pytest
from fastapi.testclient import TestClient

from memoria.accounts import AccountStore
from memoria.api import create_app

OLD = 'Temporary-current-password-2026'
NEW = 'Temporary-new-password-2026'


@pytest.fixture
def app(tmp_path):
    config = tmp_path / 'config.toml'
    config.write_text(f'[storage]\ndatabase="{(tmp_path / "unused.db").as_posix()}"\n')
    return create_app(config)


@pytest.mark.parametrize('role', ['user', 'superadmin'])
def test_change_self_preserves_identity_data_role_and_revokes_all_own_sessions(app, role):
    accounts = app.state.accounts
    user = accounts.register('temporary', OLD)
    other = accounts.register('another', OLD)
    if role == 'superadmin': accounts.set_role('temporary', role)
    old_salt = accounts.db.execute('SELECT salt FROM users WHERE id=?', (user['id'],)).fetchone()[0]
    other_token = accounts.login('another', OLD)[1]
    second = accounts.login('temporary', OLD)[1]
    with TestClient(app) as client:
        login = client.post('/api/auth/login', json={'username': 'temporary', 'password': OLD}).json()
        first = login['access_token']
        created = client.post('/api/sessions', json={'title': 'Temporary engineering history'}).json()
        result = client.post('/api/auth/password', json={'current_password': OLD, 'new_password': NEW})
        assert result.status_code == 204 and not result.content
        assert 'Max-Age=0' in result.headers['set-cookie']
        assert accounts.authenticate(first) is None and accounts.authenticate(second) is None
        assert accounts.authenticate(other_token)['id'] == other['id']
        assert client.get('/api/auth/me', headers={'Authorization': 'Bearer ' + first}).status_code == 401
        assert accounts.login('temporary', OLD) is None
        new_login = client.post('/api/auth/login', json={'username': 'temporary', 'password': NEW}).json()
        assert new_login['user'] == {**user, 'role': role}
        assert client.get('/api/sessions').json()[0]['id'] == created['id']
        row = accounts.db.execute('SELECT salt,password_hash FROM users WHERE id=?', (user['id'],)).fetchone()
        assert row['salt'] != old_salt and row['password_hash'] != NEW.encode()
        event = dict(accounts.db.execute("SELECT * FROM account_events WHERE action='self_password_change'").fetchone())
        assert event['actor_user_id'] == event['target_user_id'] == user['id']
        assert OLD not in str(event) and NEW not in str(event)


def test_wrong_old_same_new_or_invalid_fields_do_not_revoke_login_or_echo_secrets(app):
    accounts = app.state.accounts
    accounts.register('temporary', OLD)
    with TestClient(app) as client:
        client.post('/api/auth/login', json={'username': 'temporary', 'password': OLD})
        for body, status in [
            ({'current_password': 'Wrong-engineering-password', 'new_password': NEW}, 400),
            ({'current_password': OLD, 'new_password': OLD}, 400),
            ({'current_password': OLD, 'new_password': 'short'}, 422),
            ({'current_password': OLD, 'new_password': NEW, 'username': 'another'}, 422),
        ]:
            r = client.post('/api/auth/password', json=body)
            assert r.status_code == status
            assert OLD not in r.text and NEW not in r.text
            assert client.get('/api/auth/me').status_code == 200
        assert accounts.db.execute("SELECT COUNT(*) FROM account_events WHERE action='self_password_change'").fetchone()[0] == 0


def test_self_change_requires_login_and_same_origin(app):
    app.state.accounts.register('temporary', OLD)
    with TestClient(app) as client:
        body = {'current_password': OLD, 'new_password': NEW}
        assert client.post('/api/auth/password', json=body).status_code == 401
        client.post('/api/auth/login', json={'username': 'temporary', 'password': OLD})
        assert client.post('/api/auth/password', json=body, headers={'Origin': 'https://untrusted.invalid'}).status_code == 403
        assert client.post('/api/auth/password', json=body, headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403


def test_self_change_is_rate_limited_separately_from_login(app, monkeypatch):
    accounts = app.state.accounts
    accounts.register('temporary', OLD)
    def wrong(*args): raise ValueError('当前密码不正确')
    monkeypatch.setattr(accounts, 'change_password', wrong)
    with TestClient(app) as client:
        client.post('/api/auth/login', json={'username': 'temporary', 'password': OLD})
        body = {'current_password': OLD, 'new_password': NEW}
        for _ in range(10): assert client.post('/api/auth/password', json=body).status_code == 400
        assert client.post('/api/auth/password', json=body).status_code == 429
        assert client.post('/api/auth/login', json={'username': 'temporary', 'password': OLD}).status_code == 200


@pytest.mark.parametrize('interruption', ['reset', 'logout', 'expiry'])
def test_change_does_not_overwrite_reset_or_revive_revoked_session(tmp_path, monkeypatch, interruption):
    import memoria.accounts as module
    store = AccountStore(tmp_path / 'accounts.db')
    store.register('temporary', OLD)
    user, token = store.login('temporary', OLD)
    original = module.password_hash
    intercepted = False
    def interrupt(password, salt):
        nonlocal intercepted
        computed = original(password, salt)
        if password == OLD and not intercepted:
            intercepted = True
            other = AccountStore(tmp_path / 'accounts.db')
            try:
                if interruption == 'reset': other.set_password('temporary', 'Concurrent-engineering-password')
                elif interruption == 'logout': other.logout(token)
                else:
                    with other.db: other.db.execute('UPDATE login_sessions SET expires_at=0')
            finally: other.close()
        return computed
    monkeypatch.setattr(module, 'password_hash', interrupt)
    try:
        with pytest.raises(PermissionError): store.change_password(token, OLD, NEW)
        assert store.authenticate(token) is None
        assert store.login('temporary', NEW) is None
        assert store.login('temporary', 'Concurrent-engineering-password' if interruption == 'reset' else OLD)[0]['id'] == user['id']
    finally: store.close()
