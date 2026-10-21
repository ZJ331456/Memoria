"""Protocol, privacy and boundary regressions. Isolated engineering data, zero paid calls."""

import ast
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from memoria.api import create_app
from memoria.api.app import _create_tenant_app
from memoria.experiments.locomo_registration import validate_registration, scoring_identity
from memoria.experiments.roles import load_roles
from memoria.memory.reader_calls import ReaderCallLog, observe_requests
from memoria.observability.memory_trace import fingerprint, watermark
from memoria.observability.trace_metadata import compact_metadata, call_metrics
from tests.test_architecture_boundaries import tenant as tenant
from tests.test_memory_kernel import core as core, config_at

__all__ = ["tenant", "core"]

ROOT = Path(__file__).resolve().parents[1]


def contract(app):
    def leaves(router):
        for route in router.routes:
            nested = getattr(route, "original_router", None)
            if nested is not None:
                yield from leaves(nested)
            else:
                yield route

    dependencies = {}
    for route in leaves(app):
        for method in getattr(route, "methods", []):
            if hasattr(route, "dependant"):
                dependencies[(route.path, method.lower())] = sorted(
                    {
                        d.call.__module__ + "." + d.call.__qualname__
                        for d in route.dependant.dependencies
                        if d.call is not None
                    }
                )
    return [
        {
            "path": path,
            "method": method,
            "operation": operation,
            "dependencies": dependencies.get((path, method), []),
        }
        for path, operations in sorted(app.openapi()["paths"].items())
        for method, operation in sorted(operations.items())
    ]


def test_entire_tenant_contract_and_kernel_dependencies_are_frozen(tenant):
    rows = contract(tenant[0])
    expected = json.loads(
        (ROOT / "tests/fixtures/tenant_routes_v2.json").read_text(encoding="utf-8")
    )
    assert rows == expected["operations"]
    assert tenant[0].openapi().get("components", {}) == expected["components"]
    assert len(rows) == expected["count"] >= 141
    guarded = [r for r in rows if r["dependencies"]]
    assert guarded
    assert any("require_kernel" in d for r in guarded for d in r["dependencies"])


def test_schema_only_lifespan_cannot_start_a_server(tenant):
    app = _create_tenant_app(tenant[0].state.settings.source, schema_only=True)
    with pytest.raises(RuntimeError, match="cannot serve"), TestClient(app):
        pytest.fail("Schema-only app must refuse server startup")


def test_schema_only_http_is_fail_closed_without_lifespan(tenant):
    app = _create_tenant_app(tenant[0].state.settings.source, schema_only=True)
    client = TestClient(app)
    try:
        for row in contract(app):
            path = re.sub(r"\{[^}]+\}", "engineering-only", row["path"])
            response = client.request(row["method"].upper(), path, json={})
            assert response.status_code == (200 if path == "/api/health" else 503), row
        assert app.state.store is None and app.state.service is None
    finally:
        client.close()


def test_all_tenant_operations_require_actual_gateway_authentication(tmp_path):
    config = config_at(tmp_path)
    app = create_app(config)
    schema = _create_tenant_app(config, schema_only=True)
    client = TestClient(app)
    try:
        for row in contract(schema):
            path = re.sub(r"\{[^}]+\}", "engineering-only", row["path"])
            if path == "/api/health":
                continue
            response = client.request(
                row["method"].upper(),
                path,
                json={},
                headers={"X-API-Key": "forged", "X-Agent-Key": "forged"},
            )
            assert response.status_code == 401, (row, response.status_code)
        assert not app.state.tenants.apps  # Unauthenticated requests cannot instantiate a tenant.
    finally:
        client.close()
        app.state.accounts.close()


def test_request_gate_remains_active_for_every_tenant_operation(tenant):
    app = tenant[0]
    # Gate uses its own copied config; create a fresh runtime with an explicit local token.
    settings = deepcopy(app.state.settings)
    settings.api_token = "engineering-boundary-only"
    isolated = _create_tenant_app(tenant_settings=settings)
    client = TestClient(isolated)
    try:
        for row in contract(isolated):
            if row["path"] == "/api/health":
                continue
            path = re.sub(r"\{[^}]+\}", "engineering-only", row["path"])
            assert client.request(row["method"].upper(), path, json={}).status_code == 401, row
    finally:
        client.close()
        isolated.state.store.close()


@pytest.mark.parametrize("adaptive", [False, True])
def test_live_calls_are_stored_once_without_private_prompts_and_counted(tenant, adaptive):
    _, service, sid, _, _ = tenant
    secret = "private evidence " * 2000
    calls = [
        {
            "stage": "reader",
            "request": {"messages": [{"role": "user", "content": secret}]},
            "raw": secret,
            "usage": {"total_tokens": 19},
            "duration_ms": 31,
            "retries": 1,
        },
        {"stage": "support", "usage": None, "status": "failed", "error_type": "TimeoutError"},
        {"stage": "embedding", "usage": None, "status": "failed"},
    ]
    metadata = {
        "llm_calls": calls,
        "reader_calls": deepcopy(calls),
        "memory_closed_loop": {"reader": {"calls": deepcopy(calls)}, "trace_sha256": "old"},
    }
    if adaptive:
        metadata["adaptive_retrieval"] = {
            "calls": deepcopy(calls),
            "initial_candidates": [{"content": secret}],
        }
    before = service.store.observability_summary()["runtime"]
    trace = service.store.add_trace(sid, "failed", 1, 31, [], [], metadata=metadata)
    stored = trace["metadata"]
    after = service.store.observability_summary()["runtime"]
    assert after["llm_requests"] - before.get("llm_requests", 0) == 2
    assert after["llm_tokens"] - before.get("llm_tokens", 0) == 19
    assert after["llm_retries"] - before.get("llm_retries", 0) == 1
    assert after["llm_usage_unknown"] - before.get("llm_usage_unknown", 0) == 1
    assert after["embedding_requests"] - before.get("embedding_requests", 0) == 1
    assert secret not in json.dumps(stored)
    assert len(json.dumps(stored)) < 6000
    assert "reader_calls" not in stored
    assert stored["memory_closed_loop"]["reader"]["calls"] == {"ref": "llm_calls", "count": 3}
    closed = stored["memory_closed_loop"]
    assert closed["trace_sha256"] == fingerprint(
        {k: v for k, v in closed.items() if k != "trace_sha256"}
    )
    assert compact_metadata(stored) == stored
    assert metadata["llm_calls"][0]["request"]["messages"][0]["content"] == secret


def test_legacy_reader_calls_are_counted_without_double_counting():
    call = {"usage": None, "status": "failed", "retries": None}
    stored = compact_metadata({"reader_calls": [call]})
    metrics = call_metrics(stored["llm_calls"])
    assert metrics["llm_requests"] == 1 and metrics["llm_usage_unknown"] == 1
    assert metrics["llm_tokens"] == 0 and metrics["llm_retries_unknown"] == 1


def test_call_collector_keeps_failed_attempt_before_kernel_returns(tenant):
    _, service, _, _, _ = tenant
    rows = []
    from unittest.mock import AsyncMock

    service.llm.chat = AsyncMock(side_effect=TimeoutError("engineering-only"))

    async def invoke():
        with observe_requests(lambda: None, calls=rows):
            await ReaderCallLog(service.llm).chat([{"role": "user", "content": "private fixture"}])

    with pytest.raises(TimeoutError):
        asyncio.run(invoke())
    assert len(rows) == 1 and rows[0]["status"] == "failed" and rows[0]["usage"] is None


@pytest.mark.parametrize("missing", [0, 1])
def test_actual_runtime_adaptive_path_counts_gate_reader_support_once(tenant, missing):
    from tests.test_adaptive_retrieval import Model, QUERY, TEXT

    _, service, sid, _, _ = tenant
    source = service.kernel.record_message(sid, "user", TEXT)
    service.store.add_memory(TEXT, source_ref=source["id"], require_source=True)
    model = Model(service.settings, missing=missing)
    service.llm.chat = model.chat
    before = service.store.observability_summary()["runtime"]
    message, _, trace = asyncio.run(service.runtime.run(sid, QUERY, skip_memory_enqueue=True))
    after = service.store.observability_summary()["runtime"]
    calls = trace["metadata"]["llm_calls"]
    assert message["content"] == TEXT
    assert len(calls) == len(model.calls) == 3 + missing
    assert after["llm_requests"] - before.get("llm_requests", 0) == len(calls)
    assert after["llm_tokens"] - before.get("llm_tokens", 0) == 30 * len(calls)
    assert all(c.get("prompt_sha256") and "raw" not in c for c in calls)
    assert trace["metadata"]["adaptive_retrieval"]["calls"] == {
        "ref": "llm_calls",
        "count": len(calls),
    }


def test_public_transaction_preserves_nested_rollback_and_watermark(tenant):
    store = tenant[1].store
    before = watermark(store)
    with store.memory_transaction():
        store.db.execute("INSERT INTO runtime_metrics VALUES('outer-audit',1)")
        with pytest.raises(ValueError), store.memory_transaction():
            store.db.execute("INSERT INTO runtime_metrics VALUES('inner-audit',1)")
            raise ValueError("rollback inner")
    assert (
        store.db.execute("SELECT value FROM runtime_metrics WHERE key='outer-audit'").fetchone()[0]
        == 1
    )
    assert (
        store.db.execute("SELECT value FROM runtime_metrics WHERE key='inner-audit'").fetchone()
        is None
    )
    assert watermark(store)["event_seq"] == before["event_seq"]


def test_kernel_does_not_depend_on_domain_private_methods():
    tree = ast.parse((ROOT / "memoria/memory/kernel.py").read_text(encoding="utf-8"))
    bad = [
        n.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute)
        and n.attr.startswith("_")
        and not n.attr.startswith("__")
        and isinstance(n.value, ast.Attribute)
        and n.value.attr in {"store", "claims", "library", "queries"}
    ]
    assert bad == []


def test_disabled_entity_recall_keeps_identity_index_and_blocks_wrong_session(core):
    from tests.test_entity_retrieval import fixture, remember, retrieve
    from memoria.memory.library import SourceInput

    fixture(core)
    core.memory.entity_enabled = False
    item, _ = remember(core, "Memoria采用快照恢复。")
    assert (
        core.store.db.execute(
            "SELECT COUNT(*) FROM memory_entity_mentions WHERE domain='memory' AND target_id=?",
            (item["id"],),
        ).fetchone()[0]
        > 0
    )
    _, diag = retrieve(core, "Memoria", use_entities=False)
    assert "entity" not in diag["lanes"] or not diag["lanes"]["entity"]
    sid = core.store.create_session()["id"]
    core.ingest(
        SourceInput(
            kind="document",
            title="scope",
            content="private session text",
            scope="working",
            session_id=sid,
        )
    )
    hit = core.library.search("private session", session_id=sid)[0]
    assert core.dependency_valid(hit["dependency"], session_id=sid)
    assert not core.dependency_valid(hit["dependency"])


def registration_fixture(tmp_path):
    registration = json.loads(
        (ROOT / "eval/protocols/locomo-v3-diagnostic-20261022.json").read_text(encoding="utf-8")
    )
    path = tmp_path / "registration.json"
    path.write_text(json.dumps(registration), encoding="utf-8")
    protocol = {**registration["inputs"]["full"], "scope": "full"}
    settings = SimpleNamespace(
        main=SimpleNamespace(model="deepseek-flash", base_url="https://api.deepseek.com/v1")
    )
    kwargs = dict(
        fallback="adaptive_risk",
        concurrency=2,
        embedding_model="bge-small-en-v1.5",
        candidate_limit=40,
        anchor_limit=40,
        evidence_chars=12000,
    )
    return path, protocol, settings, kwargs


@pytest.mark.parametrize(
    "bad", [None, "concurrency", "reader", "gold", "copy", "scorer", "claim", "weights", "policy"]
)
def test_registered_v3_rejects_protocol_drift(tmp_path, monkeypatch, bad):
    path, protocol, settings, kwargs = registration_fixture(tmp_path)
    source = ROOT / "data/benchmarks/locomo/raw/locomo10.json"
    roles = load_roles(ROOT / "eval/protocols/locomo-v3-roles-20261022.toml")
    frozen = json.loads(path.read_text(encoding="utf-8"))
    protocol["verification_policy"] = (
        frozen["risk_verification_policy"] if bad != "policy" else {"judge_max_tokens": 4096}
    )
    monkeypatch.setattr(
        "memoria.experiments.locomo_registration.model_snapshot",
        lambda *args: {} if bad == "weights" else frozen["embedding_snapshot"],
    )
    if bad == "concurrency":
        kwargs["concurrency"] = 4
    if bad == "reader":
        roles["reader"] = replace(roles["reader"], max_tokens=512)
    if bad == "gold":
        protocol["gold_sha256"] = "wrong"
    if bad == "copy":
        source = ROOT / "locomo10.json"
    if bad == "scorer":
        monkeypatch.setattr("memoria.experiments.locomo_registration.scoring_identity", lambda: {})
    if bad == "claim":
        value = json.loads(path.read_text())
        value["claim"] = "v2 reproduction"
        path.write_text(json.dumps(value))

    def validate():
        return validate_registration(path, ROOT, source, protocol, settings, roles, **kwargs)

    if bad:
        with pytest.raises(ValueError):
            validate()
    else:
        result = validate()
        assert result["unseen_generalization"] is False and result["scoring"] == scoring_identity()


def test_full_risk_and_implicit_registered_concurrency_fail_before_any_calls():
    from memoria.experiments.locomo_eval import execute

    with pytest.raises(ValueError, match="registration"):
        asyncio.run(execute(None, None, None, fallback="adaptive_risk", scope="full"))
    with pytest.raises(ValueError, match="concurrency"):
        asyncio.run(
            execute(None, None, None, fallback="adaptive_risk", registration="engineering-only")
        )


def test_comparison_records_engineering_and_concurrency_confounds():
    from memoria.experiments.locomo_diagnostics import protocol_differences

    differences = protocol_differences(
        {"concurrency": 4, "mode": "v2"}, {"concurrency": 2, "mode": "v3"}
    )
    assert set(differences) == {"concurrency", "mode"}
    expected = {"version": "bounded-contract-repair-v1"}
    common = {
        "registration": {"id": "frozen-v3", "sha256": "same", "risk_verification_policy": expected},
        "concurrency": 2,
        "mode": "v3",
        "fallback": "none",
    }
    planned = {**common, "verification_policy": expected, "fallback": "adaptive_risk"}
    assert protocol_differences(common, planned) == {}
    assert "verification_policy" in protocol_differences(
        common, {**planned, "verification_policy": {"version": "unexpected"}}
    )
    # Another registration cannot waive a different engineering policy.
    changed = {**planned, "registration": {"id": "other", "sha256": "other"}}
    assert "verification_policy" in protocol_differences(common, changed)


def test_comparison_requires_explicit_diagnostic_opt_in(tmp_path, monkeypatch):
    from memoria.experiments import locomo_diagnostics as diagnostics
    from memoria.experiments.history import HistoryWorkspace

    baseline = {
        "run_id": "old",
        "protocol": {"source_sha256": "same", "concurrency": 4, "mode": "v2"},
        "models": {"reader": {"model": "engineering"}},
        "model_snapshots": {},
        "code": {},
        "artifacts": {},
    }
    current = deepcopy(baseline)
    current.update(
        run_id="new",
        protocol={"source_sha256": "same", "concurrency": 2, "mode": "v3", "fallback": "targeted"},
    )

    def lineage(workspace, run):
        return tmp_path / run, baseline if run == "old" else current, []

    monkeypatch.setattr(diagnostics, "lineage", lineage)
    details = [{"id": "case-1", "category": 1, "answer_f1": 1, "primary_answer_f1": 1}]
    monkeypatch.setattr(
        diagnostics,
        "read_json",
        lambda path: {"details": details} if path.name == "score.json" else {},
    )
    workspace = HistoryWorkspace(tmp_path / "data/users" / ("a" * 32))
    with pytest.raises(ValueError, match="Protocol fields differ"):
        diagnostics.compare(workspace, "old", ["new"])
    _, report = diagnostics.compare(workspace, "old", ["new"], allow_protocol_differences=True)
    assert report["comparison_limits"]["engineering_and_method_effects_separated"] is False
    assert report["comparison_limits"]["baseline_concurrency"] == 4
    assert report["new_methods"][0]["protocol_differences"]["concurrency"] == {
        "baseline": 4,
        "current": 2,
    }
