from pathlib import Path


from memoria.mcp.host import local_tool_name
from memoria.tools.builtin import build_registry
from memoria.tools.search import ToolPresentation
from memoria.store import Store


def test_local_tool_name():
    assert local_tool_name("demo", "echo") == "mcp_demo_echo"
    assert local_tool_name("my-server", "list.files").startswith("mcp_my_server_list_files_")


def test_tool_search_presentation(tmp_path: Path):
    store = Store(tmp_path / "search.db")
    registry = build_registry(store)
    presentation = ToolPresentation(registry, enabled=True)
    names = {schema["function"]["name"] for schema in presentation.schemas()}
    assert "tool_search" in names
    assert "tool_call" in names
    assert "calculate" in names
    assert "memorize" not in names
    assert "http_get" not in names
    found = presentation.search("记忆")
    assert found["matched_groups"]
    decoded = presentation.decode("tool_call", {"name": "memorize", "arguments": {"content": "hello"}})
    assert decoded == ("memorize", {"content": "hello"})
    blocked = presentation.decode("memorize", {"content": "hello"})
    assert isinstance(blocked, str) and "tool_search" in blocked
    catalog = presentation.catalog_prompt()
    assert "可搜索工具目录" in catalog and "memorize" in catalog


def test_tool_search_disabled_exposes_all(tmp_path: Path):
    store = Store(tmp_path / "nosearch.db")
    registry = build_registry(store)
    presentation = ToolPresentation(registry, enabled=False)
    names = {schema["function"]["name"] for schema in presentation.schemas()}
    assert "memorize" in names
    assert "tool_call" not in names
    assert "tool_search" not in names
