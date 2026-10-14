"""Isolated audit counterexamples; no production data or model calls."""
import asyncio

import pytest

from memoria.store import Store
from memoria.tools import ToolPolicy, build_registry


@pytest.mark.parametrize('text', [
    '不要记住这条私人信息', '请不要删除任何记忆', '我忘记上次聊了什么，请帮我找找',
    'Do you remember my preference?', 'I remember liking tea.', 'Please do not remember this.',
    "Don't forget my preference.", '请解释“记住我的偏好”是什么意思',
    '请记住红茶，但是不要记住我的偏好', '如果明天有需要，请问我是否添加记忆',
])
def test_write_policy_rejects_negation_recollection_and_quoted_commands(tmp_path, text):
    authorization = ToolPolicy().authorize(text)
    assert not authorization.allowed_write_tools
    store = Store(tmp_path / 'policy.db', 'json')
    try:
        tools = build_registry(store)
        item = store.add_memory('工程临时事实')
        for name, arguments in [('memorize', {'content': '不应保存'}),
                                ('forget_memory', {'memory_id': item['id']})]:
            result = asyncio.run(tools.execute(name, arguments,
                allowed_write_tools=authorization.allowed_write_tools))
            assert not result.ok
        assert len(store.memories()) == 1
    finally:
        store.close()


@pytest.mark.parametrize(('text', 'allowed'), [
    ('请记住我喜欢红茶', {'memorize'}), ('请帮我保存这条信息', {'memorize'}),
    ('请删除这条记忆', {'forget_memory'}), ('忘掉这条记忆', {'forget_memory'}),
    ('Please remember my preference.', {'memorize'}), ('Could you forget this memory?', {'forget_memory'}),
    ('不要删除记忆；请记住我喜欢红茶', {'memorize'}),
])
def test_write_policy_preserves_explicit_positive_commands(text, allowed):
    assert ToolPolicy().authorize(text).allowed_write_tools == allowed
