"""Temporary engineering fixtures; no production account, model, or benchmark calls."""

import ast
import asyncio
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from memoria.api.app import _create_tenant_app
from memoria.claims import AccessRequest, ClaimDraft, ClaimDecision, sha
from memoria.llm import ChatResult
from memoria.memory.access import QueryRequest
from memoria.memory.ports import MemoryReadPort, MemoryWritePort
from memoria.memory.reader import CitationGuard, SAFE_REJECTED
from memoria.provenance import message_ingress
from memoria.runtime import AgentRuntime
from memoria.tools import build_registry

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tenant(tmp_path):
    config = tmp_path / "architecture.toml"
    config.write_text(
        '[storage]\ndatabase="' + (tmp_path / "test.db").as_posix() + '"\n'
        "[memory.markdown]\nenabled=false\n[agent.mcp]\nenabled=false\n"
        '[llm.main]\nmodel="engineering-only"\napi_key="engineering-only"\n'
        'base_url="https://model.invalid/v1"\n',
        encoding="utf-8",
    )
    app = _create_tenant_app(config)
    service, store = app.state.service, app.state.store
    sid = store.create_session("Isolated architecture fixture")["id"]
    message = service.kernel.record_message(sid, "user", "我的回答语言是中文。")
    raw = message["content"]
    claim = service.claims.create(
        ClaimDraft(
            predicate="response_language",
            value="中文",
            evidence=[
                {
                    "message_id": message["id"],
                    "start": 0,
                    "end": len(raw),
                    "quote": raw,
                    "content_sha256": sha(raw),
                }
            ],
        )
    )
    service.claims.approve(
        claim["id"], ClaimDecision(expected_revision=1, reason="Engineering fixture")
    )
    query = QueryRequest(query="我用什么语言回答", session_id=sid, use_adaptive_retrieval=False)
    access = asyncio.run(service.kernel.access(query))
    ids = list(CitationGuard(service.claims, access, query).catalog)
    assert ids
    service.llm.chat = AsyncMock(
        return_value=ChatResult(
            content=json.dumps({"status": "answer", "answer": "中文", "evidence_ids": ids}),
            usage={"total_tokens": 5},
        )
    )
    service.llm.chat_stream = AsyncMock(
        side_effect=AssertionError("Strict memory answers must be buffered")
    )
    yield app, service, sid, query, message
    store.close()


@pytest.mark.parametrize("channel", ["chat", "feishu", "qq", "telegram", "api"])
def test_http_and_every_ingress_use_the_same_reader_and_kernel(tenant, channel):
    app, service, sid, query, _ = tenant
    original = service.kernel.answer
    service.kernel.answer = AsyncMock(wraps=original)
    client = TestClient(app)
    try:
        api = client.post("/api/memory-answer", json=query.model_dump(mode="json"))
        assert api.status_code == 200 and api.json()["status"] == "answer"
        assert api.json()["answer"] == "中文"
        events = []

        async def event(value):
            events.append(value)

        with message_ingress(channel):
            message, _, trace = asyncio.run(
                service.runtime.run(sid, query.query, on_event=event, skip_memory_enqueue=True)
            )
        assert service.runtime.kernel is service.kernel
        assert service.kernel.answer.await_count == 2
        assert message["content"] == api.json()["answer"]
        assert trace["metadata"]["message_origin"] == channel
        assert trace["metadata"]["citations"] == api.json()["citations"]
        assert trace["metadata"]["reader_calls"][0]["usage"]["total_tokens"] == 5
        assert [e["content"] for e in events if e["type"] == "delta"] == ["中文"]
        assert trace["metadata"]["memory_closed_loop"]["reader"]["calls"]
    finally:
        client.close()


def test_http_and_tool_state_access_share_the_pending_revision_guard(tenant):
    app, service, _, _, _ = tenant
    original = service.kernel.access_state
    service.kernel.access_state = Mock(wraps=original)
    request = AccessRequest(predicates=["response_language"])
    client = TestClient(app)
    try:
        api = client.post("/api/memory-access", json=request.model_dump(mode="json"))
        assert api.status_code == 200
        tool = asyncio.run(
            build_registry(service.kernel).execute("access_claims", request.model_dump(mode="json"))
        )
        assert tool.ok
        # The first call may compile a View which the second call then reuses.
        assert json.loads(tool.content)["path"] in {"view", "retrieve"}
        assert json.loads(tool.content)["payload"] == api.json()["payload"]
        assert service.kernel.access_state.call_count == 2
        assert tool.memory_dependencies
    finally:
        client.close()


@pytest.mark.parametrize("channel", ["feishu", "qq", "telegram"])
def test_actual_bound_bot_turn_entry_preserves_ingress_and_enqueues_formation(tenant, channel):
    app, service, sid, query, _ = tenant
    original = service.kernel.answer
    service.kernel.answer = AsyncMock(wraps=original)
    with message_ingress(channel):
        result = asyncio.run(app.state.chat_turn(sid, query.query, request_id="architecture-only"))
    assert result["message"]["content"] == "中文"
    assert result["trace"]["metadata"]["message_origin"] == channel
    assert service.kernel.answer.await_count == 1
    job = result["trace"]["metadata"]["memory_job"]
    assert job["source_ref"] == result["trace"]["metadata"]["user_message_id"]
    assert service.store.message_source(job["source_ref"])["origin"]["channel"] == channel
    assert not service._busy_check()


@pytest.mark.parametrize("change", ["hook", "source"])
def test_common_publication_rejects_changed_answer_or_withdrawn_source(tenant, change):
    _, service, _, query, message = tenant
    result = asyncio.run(service.kernel.answer(query))
    assert result["status"] == "answer"
    if change == "source":
        service.store.undo_memory_sources([message["id"]])
    checked = service.kernel.revalidate_answer(
        query, result, answer="未经审核的后置改写" if change == "hook" else result["answer"]
    )
    assert checked["answer"] == SAFE_REJECTED and checked["citations"] == []
    assert checked["validation"]["valid"] is False
    assert result["answer"] == "中文"  # The publication lease is not mutated.


def test_kernel_is_required_and_entity_repository_is_store_owned(tenant):
    _, service, _, _, _ = tenant
    assert inspect.signature(AgentRuntime).parameters["kernel"].default is inspect.Parameter.empty
    assert inspect.signature(build_registry).parameters["kernel"].default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        build_registry(None)
    assert service.kernel.entities is service.store.entities is service.store.entities
    assert "entity_catalog" not in vars(service.store)
    assert isinstance(service.kernel, MemoryReadPort)
    assert isinstance(service.kernel, MemoryWritePort)


def test_hot_models_replace_the_single_kernel_owned_embedding_client(tenant):
    _, service, _, _, _ = tenant
    previous = service.embedder
    service.update_models(
        {
            "embedding": {
                "model": "engineering-new-embedding",
                "base_url": "https://model.invalid/v1",
                "api_key": "engineering-only",
            }
        }
    )
    assert service.embedder is not previous
    assert service.embedder is service.kernel.embedder is service.runtime.memory.embedder
    assert service.kernel.queries.memory is service.kernel.memory
    assert service.memory_worker.memory is service.kernel.memory
    assert service.store.embedding_namespace == service.embedder.namespace


def test_hook_metadata_cannot_modify_the_frozen_publication_lease(tenant):
    from memoria.lifecycle import Phase

    _, service, sid, query, _ = tenant

    async def tamper(context):
        context.metadata["citations"].append({"id": "unapproved-injected-id"})
        context.metadata["memory_closed_loop"]["reader"]["status"] = "forged"

    service.runtime.pipeline.register(Phase.AFTER_REASONING, tamper)
    message, _, trace = asyncio.run(service.runtime.run(sid, query.query, skip_memory_enqueue=True))
    assert message["content"] == "中文"
    assert all(c["id"] != "unapproved-injected-id" for c in trace["metadata"]["citations"])
    assert trace["metadata"]["memory_closed_loop"]["reader"]["status"] != "forged"


def test_schema_only_kernel_routes_fail_closed(tenant):
    schema_app = _create_tenant_app(tenant[0].state.settings.source, schema_only=True)
    assert schema_app.state.store is None and schema_app.state.service is None
    client = TestClient(schema_app)
    try:
        assert client.post("/api/memory-query", json={"query": "test"}).status_code == 503
    finally:
        client.close()


def test_personal_reader_does_not_treat_unapproved_chat_history_as_evidence(tenant):
    _, service, sid, query, _ = tenant
    service.kernel.record_message(
        sid, "user", "仅为临时原话，不得作为已批准事实的独有标记_ARCH_HISTORY_"
    )
    asyncio.run(service.runtime.run(sid, query.query, skip_memory_enqueue=True))
    messages = service.llm.chat.call_args.args[0]
    assert "_ARCH_HISTORY_" not in json.dumps(messages, ensure_ascii=False)


def test_reader_telemetry_observers_are_local_to_each_task():
    from memoria.memory.reader_calls import ReaderCallLog, observe_requests, notify_request

    events = []

    async def worker(label):
        with observe_requests(lambda: events.append(label)):
            await asyncio.sleep(0)
            client = Mock()
            client.chat = AsyncMock(return_value=ChatResult(content="engineering-only"))
            await ReaderCallLog(client).chat([{"role": "user", "content": label}])

    async def run():
        await asyncio.gather(worker("one"), worker("two"))
        notify_request()

    asyncio.run(run())
    assert events == ["one", "two"]


def test_business_and_facade_modules_cannot_execute_sql():
    files = [
        *ROOT.glob("memoria/api/*.py"),
        *ROOT.glob("memoria/channels/*.py"),
        ROOT / "memoria/accounts.py",
        ROOT / "memoria/memory/kernel.py",
    ]
    for file in files:
        tree = ast.parse(file.read_text(encoding="utf-8"))
        bad = [
            n.lineno
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in {"execute", "executemany", "executescript"}
            and isinstance(n.func.value, ast.Attribute)
            and n.func.value.attr == "db"
        ]
        assert not bad, (str(file.relative_to(ROOT)), bad)


def test_business_cannot_construct_a_second_reader_or_orchestrator():
    forbidden = {
        "MemoryAccessOrchestrator",
        "read_memory_answer",
        "read_knowledge_answer",
        "MemoryEngine",
    }
    for directory in ["api", "runtime", "tools"]:
        for file in (ROOT / "memoria" / directory).glob("*.py"):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            calls = {
                n.func.id
                for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
            assert not calls & forbidden, str(file)
            assert not any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "getattr"
                and len(n.args) > 1
                and isinstance(n.args[1], ast.Constant)
                and n.args[1].value == "kernel"
                for n in ast.walk(tree)
            ), str(file)


def test_route_split_preserves_pre_refactor_http_contracts(tenant):
    app = tenant[0]
    schema = app.openapi()
    baseline = json.loads(
        (ROOT / "tests/fixtures/tenant_routes_v1.json").read_text(encoding="utf-8")
    )
    for row in baseline["routes"]:
        operation = schema["paths"][row["path"]][row["method"]]
        assert operation["operationId"].startswith(row["handler"] + "_"), row
        assert str(row["status"]) in operation["responses"], row


def test_composition_and_turn_functions_remain_bounded():
    app = ROOT / "memoria/api/app.py"
    assert len(app.read_text(encoding="utf-8").splitlines()) < 450
    agent = ast.parse((ROOT / "memoria/runtime/agent.py").read_text(encoding="utf-8"))
    run = next(
        n for n in ast.walk(agent) if isinstance(n, ast.AsyncFunctionDef) and n.name == "run"
    )
    assert run.end_lineno - run.lineno < 300
    kernel = ROOT / "memoria/memory/kernel.py"
    assert max(map(len, kernel.read_text(encoding="utf-8").splitlines())) <= 110
