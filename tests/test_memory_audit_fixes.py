"""Isolated lifecycle regressions; no production history or research scores."""
import asyncio
from datetime import datetime, timedelta, timezone
import pytest
from memoria.store import Store
from memoria.memory.engine import MemoryEngine


@pytest.mark.parametrize('backend', ['json', 'auto'])
def test_current_retrieval_excludes_future_and_expired_in_all_lanes(tmp_path, backend):
    store = Store(tmp_path/'time.db', backend)
    engine = MemoryEngine(store)
    instant = datetime.now(timezone.utc)
    live = store.add_memory('工程时间 live', embedding=[1.0, 0.0])
    future = store.add_memory('工程时间 future', embedding=[1.0, 0.0])
    expired = store.add_memory('工程时间 expired', embedding=[1.0, 0.0])
    store.set_temporal(future['id'], (instant+timedelta(days=1)).isoformat())
    store.set_temporal(expired['id'], (instant-timedelta(days=2)).isoformat(), (instant-timedelta(days=1)).isoformat())
    store.add_memory_link(live['id'], future['id'])
    store.add_memory_link(live['id'], expired['id'])
    for mode in ('keyword', 'bm25', 'rrf'):
        results = asyncio.run(engine.retrieve('工程时间', mode=mode))
        assert {m['id'] for m in results} == {live['id']}
    assert {m['id'] for m in store.keyword_memory_candidates('工程时间')} == {live['id']}
    assert not store.memory_neighbors(live['id'])
    assert {m['id'] for m, _, _ in store.chunk_memory_candidates([1.0, 0.0])} <= {live['id']}
    if store.vector_index_status['enabled']:
        assert {m['id'] for m, _ in store.vector_memory_candidates([1.0, 0.0], 1)} == {live['id']}
    assert {m['id'] for m in asyncio.run(engine.retrieve('工程时间', as_of=datetime.now(timezone.utc).isoformat()))} == {live['id']}
    store.close()


@pytest.mark.parametrize('mutation', ['delete', 'edit'])
def test_source_loss_hides_derived_memory_including_history_and_projection(tmp_path, mutation):
    from memoria.memory.layer import MarkdownMemoryLayer
    store = Store(tmp_path/'source.db', 'json')
    session = store.create_session()['id']
    source = store.add_message(session, 'user', '工程原话证据')
    memory = store.add_memory(source['content'], source='reviewed_conversation', source_ref=source['id'], require_source=True)
    layer = MarkdownMemoryLayer(tmp_path/'markdown')
    engine = MemoryEngine(store, markdown=layer)
    assert '工程原话证据' in engine.refresh_markdown()
    if mutation == 'delete':
        store.delete_session(session)
    else:
        store.db.execute('UPDATE messages SET content=? WHERE id=?', ('被编辑的原文', source['id']))
        store.db.commit()
    assert not asyncio.run(engine.retrieve('工程原话'))
    assert not asyncio.run(engine.retrieve('工程原话', as_of=memory['created_at']))
    assert '工程原话证据' not in engine.refresh_markdown()
    assert store.memory(memory['id'])  # Preserve audit state without treating it as usable evidence.
    store.close()


def test_revoked_source_cannot_create_replace_or_reinforce_after_restart(tmp_path):
    from memoria.store import MemorySourceUnavailable
    path = tmp_path/'revoke.db'
    store = Store(path, 'json')
    source = store.add_message(store.create_session()['id'], 'user', '工程撤销样例')
    first = store.add_memory('工程撤销样例', source_ref=source['id'], require_source=True)
    other = store.add_memory('独立事实')
    store.undo_memory_sources([source['id']])
    store.close()

    store = Store(path, 'json')
    with pytest.raises(MemorySourceUnavailable):
        asyncio.run(MemoryEngine(store).remember('工程撤销样例', 'fact', 3, 'reviewed_conversation', source['id'], require_source=True))
    with pytest.raises(MemorySourceUnavailable):
        store.add_memory('替代事实', supersedes_id=other['id'], source_ref=source['id'])
    with pytest.raises(MemorySourceUnavailable):
        store.reinforce_memory(other['id'], source['id'])
    assert store.memory(other['id'])['status']=='active'
    assert store.memory(other['id'])['reinforcement']==1
    assert not asyncio.run(MemoryEngine(store).retrieve('撤销样例'))
    assert store.memory(first['id'])['status']=='superseded'
    store.close()


def test_query_embedding_race_never_returns_deleted_snapshot(tmp_path):
    class Gated:
        enabled=True; namespace='engineering'; timeout_seconds=1
        async def embed(self,text):
            started.set()
            await release.wait()
            return [1.0,0.0]
    async def case():
        nonlocal started,release
        started,release=asyncio.Event(),asyncio.Event()
        store=Store(tmp_path/'race.db','json')
        engine=MemoryEngine(store,Gated())
        item=store.add_memory('工程查询竞态',embedding=[1.0,0.0])
        task=asyncio.create_task(engine.retrieve('工程查询'))
        await asyncio.wait_for(started.wait(),2)
        store.delete_memory(item['id'])
        release.set()
        assert await task==[]
        store.close()
    started=release=None
    asyncio.run(case())
