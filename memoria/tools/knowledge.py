"""Conversation tools are clients of the memory kernel, not owners of storage policy."""
from ..memory.records import MemorySearch
from ..observability.context import current_session_id
from .registry import Tool, ToolValue


def register_knowledge_tools(tools, kernel):
    async def search(arguments, *, library_only=False):
        # Neither account identity nor working-session scope can be chosen by model arguments.
        request = MemorySearch(query=arguments['query'], session_id=current_session_id.get(),
            domains=['library'] if library_only else arguments.get('domains', ['memory', 'library']),
            limit=arguments.get('limit', 4), budget_chars=6000)
        result = await kernel.search(request)
        return ToolValue(result, result['dependencies'])

    async def library(arguments):
        return await search(arguments, library_only=True)

    common = {'query': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
              'limit': {'type': 'integer', 'minimum': 1, 'maximum': 8}}
    tools.register(Tool('search_knowledge',
        '按来源检索个人记忆、文档/笔记/任务资料；需要原话时显式选择conversation。当前事实/条件冲突使用access_claims。',
        {'type': 'object', 'properties': common | {'domains': {'type': 'array', 'minItems': 1, 'maxItems': 3,
             'items': {'type': 'string', 'enum': ['memory', 'conversation', 'library']}}},
         'required': ['query'], 'additionalProperties': False}, search, max_output_chars=16000,
        search_hint='资料 文档 任务 记忆 来源 knowledge evidence'))
    tools.register(Tool('search_library', '检索笔记、文档、任务结果；兼容入口，统一来源检索使用search_knowledge。',
        {'type': 'object', 'properties': common, 'required': ['query'], 'additionalProperties': False},
        library, max_output_chars=16000, always_on=False))
