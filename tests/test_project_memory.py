"""DevMemory project workspaces: engineering verification on isolated temp data.

Covers the read-only git adapter on a synthetic repository, the workspace
registry/task loop over the untouched memory core, tenant API routes, agent tool
registration and the DevMemory MCP stdio protocol handler. No model calls: the
engineering kernel has no LLM, which is exactly how the distill guard is checked.
"""

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from memoria.api.app import _create_tenant_app
from memoria.config import Settings
from memoria.memory.library import LibraryError
from memoria.projects import ProjectError, ProjectWorkspaces, collect_git_history
from memoria.store import Store
from memoria.tools.builtin import build_registry
from memoria.tools.projects import register_project_tools
from scripts.devmemory_mcp_server import DevMemoryServer, MemoriaHttpBackend
from tests.kernel_factory import tool_kernel
from tests.test_memory_kernel import config_at


def make_repo(path: Path) -> str:
    path.mkdir(parents=True)
    def git(*args, env_extra=None):
        env = dict(os.environ, **(env_extra or {}))
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, env=env)
    git("init", "-q")
    git("config", "user.name", "工程测试")
    git("config", "user.email", "test@example.com")
    stamp = {"GIT_AUTHOR_DATE": "2026-01-01T09:00:00+08:00", "GIT_COMMITTER_DATE": "2026-01-01T09:00:00+08:00"}
    (path / "README.md").write_text("# 演示仓库\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "init: 初始提交", env_extra=stamp)
    stamp = {"GIT_AUTHOR_DATE": "2026-10-01T09:00:00+08:00", "GIT_COMMITTER_DATE": "2026-10-01T09:00:00+08:00"}
    (path / "notes.txt").write_text("决定采用SQLite，方案A依赖冲突已放弃\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "feat: 选定存储方案", "-m", "方案A依赖冲突已放弃，采用方案C", env_extra=stamp)
    head = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()
    return head


@pytest.fixture
def workspaces(tmp_path):
    settings = Settings.load(config_at(tmp_path))
    store = Store(settings.database, "json")
    kernel = tool_kernel(store)
    try:
        yield ProjectWorkspaces(store, kernel)
    finally:
        store.close()


# ---- git adapter -----------------------------------------------------------

def test_git_export_contains_commits_and_provenance(tmp_path):
    head = make_repo(tmp_path / "演示仓库")
    result = collect_git_history(str(tmp_path / "演示仓库"))
    assert result["head"] == head and result["count"] == 2
    assert "feat: 选定存储方案" in result["text"] and "方案A依赖冲突已放弃" in result["text"]
    assert "2026-10-01" in result["text"]  # commit time lives in the text itself
    assert "导出时间不补齐为事件时间" in result["text"]


def test_git_export_since_and_body_limits(tmp_path):
    make_repo(tmp_path / "repo")
    limited = collect_git_history(str(tmp_path / "repo"), since="2026-06-01")
    assert limited["count"] == 1 and limited["title"].endswith("（1 个提交）")
    assert "初始提交" not in limited["text"] and "选定存储方案" in limited["text"]
    no_body = collect_git_history(str(tmp_path / "repo"), include_body=False)
    assert "采用方案C" not in no_body["text"]


def test_git_export_rejects_missing_or_empty_repositories(tmp_path):
    (tmp_path / "plain").mkdir()
    with pytest.raises(ProjectError) as not_git:
        collect_git_history(str(tmp_path / "plain"))
    assert not_git.value.status == 422
    (tmp_path / "empty").mkdir()
    subprocess.run(["git", "-C", str(tmp_path / "empty"), "init", "-q"], capture_output=True, check=True)
    with pytest.raises(ProjectError) as empty:
        collect_git_history(str(tmp_path / "empty"))
    assert "git log 失败" in str(empty.value)


# ---- workspace loop --------------------------------------------------------

def test_project_create_and_git_import_bind_sources(workspaces, tmp_path):
    head = make_repo(tmp_path / "repo")
    project = workspaces.create("演示项目", repo_path=str(tmp_path / "repo"), description="验收用")
    result = workspaces.import_git(project["id"])
    assert result["commits"] == 2 and result["head"] == head
    source = result["source"]
    assert source["kind"] == "document" and source["authorship"] == "external"
    assert source["scope"] == "personal" and source["session_id"] is None
    assert source["origin"] == f"git-log:repo@{head[:12]}"
    assert "source_time" not in source["revision"]  # the export never claims an event time
    detail = workspaces.detail(project["id"])
    assert detail["sources"][0]["live_status"] == "active"
    assert detail["sources"][0]["source_id"] == source["id"]
    assert workspaces.store.sessions() == []  # no chat messages were fabricated


def test_project_import_git_requires_repo_path(workspaces, tmp_path):
    project = workspaces.create("空项目")
    with pytest.raises(ProjectError) as missing:
        workspaces.import_git(project["id"])
    assert missing.value.status == 422
    make_repo(tmp_path / "repo")
    workspaces.archive(project["id"])
    with pytest.raises(ProjectError) as archived:
        workspaces.import_git(project["id"])
    assert archived.value.status == 409


def test_import_text_rejects_task_result_kind(workspaces):
    project = workspaces.create("文本项目")
    with pytest.raises(ProjectError) as wrong:
        workspaces.import_text(project["id"], title="t", content="c", kind="task_result")
    assert wrong.value.status == 422
    source = workspaces.import_text(project["id"], title="架构决策", content="采用RRF混合检索")
    assert source["kind"] == "document" and source["origin"] == f"project:{project['id']}"


def test_duplicate_project_name_conflicts(workspaces):
    workspaces.create("同名项目")
    with pytest.raises(ProjectError) as conflict:
        workspaces.create("同名项目")
    assert conflict.value.status == 409


def test_task_loop_records_result_feedback_and_filters_recall(workspaces, tmp_path):
    make_repo(tmp_path / "repo")
    project = workspaces.create("闭环项目", repo_path=str(tmp_path / "repo"))
    workspaces.import_git(project["id"])
    task = workspaces.create_task(project["id"], title="修复依赖冲突", detail="方案A再试一次")
    finished = workspaces.finish_task(
        project["id"], task["id"], outcome="failure", content="方案A尝试失败：出现循环依赖，改用方案C"
    )
    assert finished["task"]["status"] == "done" and finished["task"]["source_id"]
    source = finished["source"]
    assert source["kind"] == "task_result" and source["authorship"] == "unknown"
    assert source["origin"] == f"project-task:{project['id']}:{task['id']}"
    assert source["feedback"]["outcome"] == "failure"
    with pytest.raises(ProjectError) as twice:
        workspaces.finish_task(project["id"], task["id"], outcome="success", content="再次报告")
    assert twice.value.status == 409
    hits = workspaces.recall(project["id"], "循环依赖 方案C")
    assert hits["hits"] and hits["open_tasks"] == []
    assert all(hit["source_id"] in {s["source_id"] for s in workspaces.detail(project["id"])["sources"]}
               for hit in hits["hits"])
    other = workspaces.create("无关项目")
    empty = workspaces.recall(other["id"], "循环依赖 方案C")
    assert empty["hits"] == [] and empty["open_tasks"] == []


def test_task_distill_requires_llm_and_never_runs_automatically(workspaces):
    project = workspaces.create("提炼项目")
    task = workspaces.create_task(project["id"], title="整理经验")
    with pytest.raises(ProjectError) as pending:
        asyncio.run(workspaces.distill_task(project["id"], task["id"]))
    assert pending.value.status == 409  # no result recorded yet
    workspaces.finish_task(project["id"], task["id"], outcome="unknown", content="结果待定")
    with pytest.raises(LibraryError) as no_llm:
        asyncio.run(workspaces.distill_task(project["id"], task["id"]))
    assert no_llm.value.status == 503  # engineering kernel has no LLM; business layer never calls one


def test_resolve_and_archive(workspaces):
    project = workspaces.create("解析项目")
    assert workspaces.resolve("解析项目") == project["id"]
    assert workspaces.resolve(project["id"]) == project["id"]
    with pytest.raises(ProjectError):
        workspaces.resolve("不存在")
    workspaces.archive(project["id"])
    assert workspaces.list() == []
    with pytest.raises(ProjectError) as archived:
        workspaces.resolve("解析项目")
    assert archived.value.status == 409


# ---- tenant API ------------------------------------------------------------

def test_project_api_routes(tmp_path):
    make_repo(tmp_path / "repo")
    app = _create_tenant_app(config_at(tmp_path))
    try:
        client = TestClient(app)
        created = client.post("/api/projects", json={
            "name": "接口项目", "repo_path": str(tmp_path / "repo"), "description": "d"})
        assert created.status_code == 201, created.text
        pid = created.json()["id"]
        assert client.post("/api/projects", json={"name": "接口项目"}).status_code == 409
        assert client.post("/api/projects", json={}).status_code == 422
        listing = client.get("/api/projects").json()
        assert listing[0]["open_tasks"] == 0 and listing[0]["sources"] == 0
        imported = client.post(f"/api/projects/{pid}/import/git", json={"max_commits": 10})
        assert imported.status_code == 200 and imported.json()["commits"] == 2
        task = client.post(f"/api/projects/{pid}/tasks", json={"title": "上线前检查"})
        assert task.status_code == 201
        finished = client.post(f"/api/projects/{pid}/tasks/{task.json()['id']}/finish",
                               json={"outcome": "success", "content": "检查通过，未发现回归"})
        assert finished.status_code == 200 and finished.json()["outcome"] == "success"
        recall = client.post(f"/api/projects/{pid}/recall", json={"query": "存储方案"})
        assert recall.status_code == 200 and recall.json()["hits"]
        file_upload = client.post(
            f"/api/projects/{pid}/import/file",
            params={"filename": "设计.txt"},
            content="采用RRF混合检索".encode("utf-8"),
            headers={"Content-Type": "application/octet-stream"})
        assert file_upload.status_code == 201, file_upload.text
        assert client.post(f"/api/projects/{pid}/import/file",
                           params={"filename": "bad.pdf"}, content=b"%PDF").status_code == 422
        detail = client.get(f"/api/projects/{pid}").json()
        assert {item["kind"] for item in detail["sources"]} == {"document", "task_result"}
        missing = client.post("/api/projects/unknown/recall", json={"query": "x"})
        assert missing.status_code == 404
        archived = client.delete(f"/api/projects/{pid}")
        assert archived.status_code == 200 and client.get("/api/projects").json() == []
    finally:
        app.state.store.close()


def test_project_api_schema_only_declares_routes(tmp_path):
    schema = _create_tenant_app(config_at(tmp_path / "schema"), schema_only=True).openapi()
    for path in ("/api/projects", "/api/projects/{pid}/import/git",
                 "/api/projects/{pid}/tasks/{tid}/finish", "/api/projects/{pid}/recall"):
        assert path in schema["paths"]
    assert schema["paths"]["/api/projects/{pid}/import/file"]["post"]["requestBody"][
        "content"]["application/octet-stream"]["schema"]["format"] == "binary"


# ---- agent tools -----------------------------------------------------------

def test_agent_tools_are_registered_and_executable(tmp_path):
    settings = Settings.load(config_at(tmp_path))
    store = Store(settings.database, "json")
    try:
        kernel = tool_kernel(store)
        workspaces = ProjectWorkspaces(store, kernel)
        workspaces.create("工具项目")
        registry = build_registry(kernel)
        register_project_tools(registry, workspaces)
        names = {tool.name for tool in registry.all()}
        assert {"project_list", "project_recall", "project_record_task"} <= names
        recorded = asyncio.run(registry.execute("project_record_task", {
            "project": "工具项目", "title": "修复检索回归",
            "content": "定位到缓存前缀抖动，已修复并复测", "outcome": "success"}, allow_write=True))
        assert recorded.ok, recorded.content
        payload = json.loads(recorded.content)
        assert payload["outcome"] == "success" and payload["source_id"]
        recalled = asyncio.run(registry.execute(
            "project_recall", {"project": "工具项目", "query": "缓存前缀"}))
        assert recalled.ok and "缓存前缀" in recalled.content
        listed = asyncio.run(registry.execute("project_list", {}))
        assert listed.ok and "工具项目" in listed.content
    finally:
        store.close()


# ---- MCP stdio protocol ----------------------------------------------------

class FakeBackend:
    """Same interface as MemoriaHttpBackend without any network traffic."""

    def __init__(self):
        self.default_project = ""
        self.calls = []

    def _resolve(self, project):
        return "p1"

    def list_projects(self, args):
        self.calls.append("list")
        return {"projects": [{"id": "p1", "name": "演示项目", "sources": 2, "open_tasks": 1}]}

    def project_context(self, args):
        return {"id": "p1", "name": "演示项目", "sources": [], "tasks": []}

    def recall_project_memory(self, args):
        self.calls.append(("recall", args["project"], args["query"]))
        return {"hits": [{"title": "Git 提交历史", "quote": "采用方案C"}], "open_tasks": []}

    def record_task_result(self, args):
        return {"task": {"id": "t1", "status": "done"}, "outcome": args["outcome"]}


def test_mcp_server_initialize_list_and_notifications():
    server = DevMemoryServer(FakeBackend())
    greeting = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert greeting["result"]["protocolVersion"] == "2024-11-05"
    assert greeting["result"]["serverInfo"]["name"] == "memoria-devmemory"
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert {item["name"] for item in tools["result"]["tools"]} == {
        "list_projects", "project_context", "recall_project_memory", "record_task_result"}
    assert server.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})["error"]["code"] == -32601
    bad = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                         "params": {"name": "nope"}})
    assert bad["error"]["code"] == -32602


def test_mcp_server_tool_calls_succeed_and_fail_as_results():
    backend, server = FakeBackend(), DevMemoryServer(FakeBackend())
    server.backend = backend
    recalled = server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
        "name": "recall_project_memory",
        "arguments": {"project": "演示项目", "query": "存储方案"}}})
    payload = json.loads(recalled["result"]["content"][0]["text"])
    assert payload["hits"][0]["quote"] == "采用方案C"
    assert backend.calls == [("recall", "演示项目", "存储方案")]

    class Broken(FakeBackend):
        def recall_project_memory(self, args):
            raise RuntimeError("项目不存在：x")

    failing = DevMemoryServer(Broken())
    errored = failing.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {
        "name": "recall_project_memory", "arguments": {"project": "x", "query": "y"}}})
    assert errored["result"]["isError"] is True
    assert "项目不存在" in errored["result"]["content"][0]["text"]


def test_mcp_http_backend_normalizes_base_and_maps_connection_failure():
    backend = MemoriaHttpBackend("http://127.0.0.1:2237/api/", "u", "p")
    assert backend.base == "http://127.0.0.1:2237"
    with pytest.raises(RuntimeError) as denied:
        backend.login()  # nothing listens on the port; the failure must name the cause
    assert "无法连接" in str(denied.value) or "登录失败" in str(denied.value)


def test_mcp_server_rejects_non_jsonrpc_frames():
    server = DevMemoryServer(FakeBackend())
    assert server.handle({"method": "initialize", "id": 1})["error"]["code"] == -32600
