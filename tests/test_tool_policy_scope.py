"""Authorization applies to the requested operation, not verbs in its payload."""
import asyncio

import pytest

from memoria.store import Store
from memoria.tools import ToolPolicy, build_registry


@pytest.mark.parametrize(('text', 'allowed'), [
    ('请记住我的习惯是忘记锁门', {'memorize'}),
    ('Please remember I tend to forget my keys', {'memorize'}),
    ('请删除包含记住两个字的记忆', {'forget_memory'}),
    ('Please forget the memory where I said remember', {'forget_memory'}),
    ('Can you remember my name?', set()), ('Remember our last conversation?', set()),
    ('你还记住我的名字了吗？', set()), ('记住我的名字了吗？', set()),
    ('Could you please remember my preference?', {'memorize'}),
    ('Can you memorize this fact?', {'memorize'}),
])
def test_commands_cannot_authorize_other_verbs_in_the_saved_fact(tmp_path, text, allowed):
    authorization = ToolPolicy().authorize(text)
    assert authorization.allowed_write_tools == allowed
    store = Store(tmp_path / 'scope.db', 'json')
    try:
        tools = build_registry(store)
        item = store.add_memory('Engineering existing fact')
        if 'forget_memory' not in allowed:
            result = asyncio.run(tools.execute('forget_memory', {'memory_id': item['id']},
                allowed_write_tools=authorization.allowed_write_tools))
            assert not result.ok and store.memory(item['id'])
        if 'memorize' not in allowed:
            result = asyncio.run(tools.execute('memorize', {'content': 'Must not be saved'},
                allowed_write_tools=authorization.allowed_write_tools))
            assert not result.ok and len(store.memories()) == 1
    finally:
        store.close()
