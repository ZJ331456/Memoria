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
