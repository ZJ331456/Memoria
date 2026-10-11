import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from memoria.api import create_app
from memoria.mcp.client import McpClient
from memoria.mcp.config import McpServerConfig, load_mcp_servers
from memoria.mcp.host import McpHost, local_tool_name
from memoria.tools.registry import ToolRegistry


def test_tool_names_fit_small_model_protocol_and_do_not_collide():
    assert len(local_tool_name("github", "a" * 150)) <= 64
    assert local_tool_name("wiki", "file.list") != local_tool_name("wiki", "file-list")
    assert local_tool_name("github", "a" * 150) != local_tool_name("github", "a" * 149 + "b")


@pytest.mark.parametrize("bad", [{"timeout_seconds": 0}, {"timeout_seconds": float("nan")}, {"enabled": "false"}])
def test_invalid_config_cannot_hang_or_unexpectedly_enable_server(tmp_path, bad):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": [{"name": "search", "command": ["runtime"], **bad}]}))
    with pytest.raises(ValueError):
        load_mcp_servers(path)


@pytest.mark.parametrize("cycle", [False, True])
def test_paginated_tools_and_cursor_cycle_cleanup(monkeypatch, cycle):
    async def run():
        client = McpClient("search", ("runtime",))
        process = type("Process", (), {"returncode": None})()
        monkeypatch.setattr("memoria.mcp.client.asyncio.create_subprocess_exec", AsyncMock(return_value=process))
        client._drain_stderr = AsyncMock()
        client._notify = AsyncMock()
        client._force_cleanup = AsyncMock()
        tool = lambda name: {"name": name, "inputSchema": {"type": "object"}}
        client._request = AsyncMock(side_effect=[{"protocolVersion": "2025-11-25"},
            {"tools": [tool("search")], "nextCursor": "page2"},
            {"tools": [tool("fetch_content")], **({"nextCursor": "page2"} if cycle else {})}])
        if cycle:
            with pytest.raises(RuntimeError, match="cursor"):
                await client.connect()
            client._force_cleanup.assert_awaited_once()
        else:
            assert [t.name for t in await client.connect()] == ["search", "fetch_content"]
            assert client._request.call_args_list[-1].args == ("tools/list", {"cursor": "page2"})
        await asyncio.gather(client._stderr_task, return_exceptions=True)
    asyncio.run(run())


def test_server_ping_with_same_id_is_not_mistaken_for_response():
    async def run():
        reader = asyncio.StreamReader()
        for payload in [{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                        {"jsonrpc": "2.0", "id": 9, "method": "sampling/createMessage", "params": {}},
                        {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}]:
            reader.feed_data((json.dumps(payload) + "\n").encode())
        client = McpClient("github", ("runtime",))
        client._process = type("Process", (), {"stdout": reader})()
        client._send = AsyncMock()
        response = await client._recv(expected_id=1, timeout=1)
        assert response["result"] == {"tools": []}
        sent = [c.args[0] for c in client._send.call_args_list]
        assert sent[0] == {"jsonrpc": "2.0", "id": 1, "result": {}}
        assert sent[1]["error"]["code"] == -32601
    asyncio.run(run())


def test_timeout_closes_owned_server():
    async def run():
        client = McpClient("search", ("runtime",))
        client._send = AsyncMock()
        client._recv = AsyncMock(side_effect=TimeoutError)
        client._force_cleanup = AsyncMock()
        with pytest.raises(TimeoutError):
            await client._request("tools/call", {}, timeout=.1)
        client._force_cleanup.assert_awaited_once()
    asyncio.run(run())


def test_headless_entry_and_unknown_api_not_caught_by_spa(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(f'[storage]\ndatabase="{(tmp_path / "service.db").as_posix()}"\n[agent.mcp]\nenabled=false\n')
    with TestClient(create_app(config)) as client:
        assert client.get("/").json()["memories"] == "/api/memories"
        assert client.get("/api/nonexistent").status_code == 404
        assert client.get("/openapi.json").status_code == 200


def test_cancelled_host_start_closes_already_connected_clients(tmp_path, monkeypatch):
    async def run():
        host = McpHost(ToolRegistry(), tmp_path / "mcp.json")
        host._configs = {name: McpServerConfig(name=name, command=("runtime",)) for name in ("fast", "slow")}
        slow_started = asyncio.Event()
        fast_connected = asyncio.Event()
        connected = type("Client", (), {"disconnect": AsyncMock()})()

        async def connect(config):
            if config.name == "fast":
                host._clients["fast"] = connected
                fast_connected.set()
                return None
            slow_started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(host, "_connect_one", connect)
        task = asyncio.create_task(host.connect_all())
        await slow_started.wait()
        await fast_connected.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        connected.disconnect.assert_awaited_once()
        assert not host._clients
    asyncio.run(run())
