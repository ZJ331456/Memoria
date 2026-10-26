"""Project-memory tools for the agent loop.

The agent can list projects, recall project context before starting work and
record a finished task with explicit outcome feedback. Imports and distillation
stay on the Studio/API side; the tool layer never calls the model itself.
"""

from __future__ import annotations

from .registry import Tool


def _schema(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _hit_line(hit: dict) -> dict:
    cards = [
        {
            "content": card.get("content", ""),
            "applicability": card.get("applicability", ""),
            "reported_outcome": card.get("reported_outcome"),
            "citation": card.get("citation", ""),
        }
        for card in hit.get("knowledge_cards", [])
    ]
    return {
        "title": hit.get("title", ""),
        "quote": hit.get("quote", ""),
        "citation": hit.get("citation", ""),
        "knowledge_cards": cards,
    }


def register_project_tools(registry, workspaces) -> None:
    if workspaces is None:
        return

    async def project_list(a):
        return workspaces.list()

    async def project_recall(a):
        pid = workspaces.resolve(str(a["project"]))
        result = workspaces.recall(pid, str(a["query"]), limit=int(a.get("limit", 6)))
        return {
            "project": str(a["project"]),
            "hits": [_hit_line(hit) for hit in result["hits"]],
            "project_sources": result["project_sources"],
            "open_tasks": result["open_tasks"],
            "note": "任务前请先引用这些来源；缺失不代表事实不存在，可换关键词再查或明确说不知道。",
        }

    async def project_record_task(a):
        pid = workspaces.resolve(str(a["project"]))
        task = workspaces.create_task(pid, title=str(a["title"]), detail=str(a.get("detail", "")))
        finished = workspaces.finish_task(
            pid, task["id"], outcome=str(a["outcome"]), content=str(a["content"])
        )
        return {
            "task_id": task["id"],
            "source_id": finished["source"]["id"],
            "revision_id": finished["source"]["revision"]["id"],
            "outcome": finished["outcome"],
            "note": "结果与反馈已入库；流程完成不代表任务成功，经验提炼需在 Studio 显式触发。",
        }

    registry.register(Tool(
        "project_list", "列出当前账号的项目记忆工作区（名称、来源数、待办任务数）。",
        _schema({}, []),
        project_list, search_hint="项目 列表 devmemory project", always_on=False,
    ))
    registry.register(Tool(
        "project_recall",
        "在指定项目内检索项目记忆：提交历史、设计文档、任务结果与经验卡片，任务开始前先用它召回背景。",
        _schema({
            "project": {"type": "string", "minLength": 1, "maxLength": 80},
            "query": {"type": "string", "minLength": 1, "maxLength": 300},
            "limit": {"type": "integer", "minimum": 1, "maximum": 8},
        }, ["project", "query"]),
        project_recall, max_output_chars=20000,
        search_hint="项目记忆 决策 之前尝试 project recall", always_on=False,
    ))
    registry.register(Tool(
        "project_record_task",
        "任务结束后把结果写入项目记忆：创建任务并记录结果与验收反馈（success/failure/unknown）。",
        _schema({
            "project": {"type": "string", "minLength": 1, "maxLength": 80},
            "title": {"type": "string", "minLength": 1, "maxLength": 120},
            "content": {"type": "string", "minLength": 1, "maxLength": 3000},
            "outcome": {"type": "string", "enum": ["success", "failure", "unknown"]},
            "detail": {"type": "string", "maxLength": 4000},
        }, ["project", "title", "content", "outcome"]),
        project_record_task, "write", max_output_chars=8000,
        search_hint="记录任务 项目结果 project task", always_on=False,
    ))
