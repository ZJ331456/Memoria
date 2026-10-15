"""Temporary engineering stores; never writes personal data or calls a model."""
import asyncio
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

from memoria.api.gateway import TenantPool
from memoria.config import Settings
from memoria.service import AgentService


def test_pending_integrations_do_not_block_data_chat_or_another_account(tmp_path, monkeypatch):
    async def run():
        entered, cancelled = set(), set()

        async def slow_start(service):
            entered.add(service.claims.user_id)
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.add(service.claims.user_id)

        monkeypatch.setattr(AgentService, 'start_integrations', slow_start)
        config = tmp_path / 'config.toml'
        config.write_text('[storage]\ndatabase="' + (tmp_path / 'unused.db').as_posix() + '"\n')
        pool = TenantPool(Settings.load(config), tmp_path)
        owners = [dict(id=uuid.uuid4().hex, username=name) for name in ('first', 'second')]
        try:
            for index, owner in enumerate(owners):
                app = await asyncio.wait_for(pool.get(owner), timeout=2)
                await asyncio.sleep(0)
                assert owner['id'] in entered
                service = app.state.service
                assert service.integration_status()['state'] == 'starting'
                sid = app.state.store.create_session('Engineering record')['id']
                app.state.store.add_message(sid, 'user', 'Temporary engineering input')
                async def reply(*args, **kwargs):
                    return {'content': 'Engineering reply'}, [], {}
                service.chat_with_trace = reply
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                    summary = await asyncio.wait_for(client.get('/api/studio/summary'), 2)
                    assert summary.status_code == 200
                    assert summary.json()['sessions'] == 1  # Never includes the other tenant.
                    assert summary.json()['messages'] == 1
                    status = await client.get('/api/mcp')
                    assert status.json()['startup']['state'] == 'starting'
                    chat = await asyncio.wait_for(client.post('/api/sessions/' + sid + '/chat/stream', json={'content': 'engineering'}), 2)
                    assert '"type": "complete"' in chat.text
        finally:
            await asyncio.wait_for(pool.close(), 2)
        assert cancelled == {o['id'] for o in owners}
    asyncio.run(run())


def test_failed_optional_initialization_is_safe_and_data_remains_available(tmp_path, monkeypatch):
    async def run():
        async def failure(service):
            raise RuntimeError('private provider error must not be exposed')
        monkeypatch.setattr(AgentService, 'start_integrations', failure)
        config = tmp_path / 'config.toml'
        config.write_text('[storage]\ndatabase="' + (tmp_path / 'unused.db').as_posix() + '"\n')
        pool = TenantPool(Settings.load(config), tmp_path)
        try:
            app = await pool.get(dict(id=uuid.uuid4().hex, username='temporary'))
            await asyncio.sleep(0)
            assert app.state.service.integration_status() == {'state': 'failed', 'error': 'RuntimeError'}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                assert (await client.get('/api/studio/conversations')).status_code == 200
        finally:
            await pool.close()
    asyncio.run(run())


def test_manual_mcp_reload_waits_for_startup_then_runs_once(tmp_path, monkeypatch):
    async def run():
        release = asyncio.Event()
        async def slow_start(service):
            await release.wait()
        monkeypatch.setattr(AgentService, 'start_integrations', slow_start)
        config = tmp_path / 'config.toml'
        config.write_text('[storage]\ndatabase="' + (tmp_path / 'unused.db').as_posix() + '"\n')
        pool = TenantPool(Settings.load(config), tmp_path)
        try:
            app = await pool.get(dict(id=uuid.uuid4().hex, username='temporary'))
            service = app.state.service
            service.mcp.reload = AsyncMock(return_value=[])
            reload_task = asyncio.create_task(service.reload_mcp())
            await asyncio.sleep(0)
            service.mcp.reload.assert_not_called()
            release.set()
            await asyncio.wait_for(reload_task, 2)
            service.mcp.reload.assert_awaited_once()
        finally:
            await pool.close()
    asyncio.run(run())


@pytest.mark.parametrize('connected,expected', [(True, 'ready'), (False, 'degraded')])
def test_initialized_but_unavailable_mcp_is_reported_as_degraded(tmp_path, monkeypatch, connected, expected):
    async def run():
        monkeypatch.setattr(AgentService, 'start_integrations', AsyncMock())
        config = tmp_path / 'config.toml'
        config.write_text('[storage]\ndatabase="' + (tmp_path / 'unused.db').as_posix() + '"\n')
        pool = TenantPool(Settings.load(config), tmp_path)
        try:
            app = await pool.get(dict(id=uuid.uuid4().hex, username='temporary'))
            service = app.state.service
            monkeypatch.setattr(service.mcp, 'status', lambda: {'servers': [{'enabled': True, 'connected': connected}]})
            await asyncio.sleep(0)
            assert service.integration_status() == {'state': expected, 'error': ''}
        finally:
            await pool.close()
    asyncio.run(run())
