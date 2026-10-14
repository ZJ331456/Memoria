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


def test_concurrent_exact_writes_recheck_inside_transaction(tmp_path):
    class Embed:
        enabled=True; namespace='engineering'; timeout_seconds=1
        async def embed(self,text):
            await asyncio.sleep(0)
            return [1.0,0.0]
    store=Store(tmp_path/'dedup.db','json')
    engine=MemoryEngine(store,Embed())
    async def case():
        return await asyncio.gather(*(engine.remember('相同工程事实','fact',3,'test') for _ in range(2)))
    results=asyncio.run(case())
    assert sorted(r.action for r in results)==['created','reinforced']
    assert len(store.memories())==1
    store.close()


def test_cross_connection_exact_writes_and_word_boundaries(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path=tmp_path/'connections.db'
    a,b=Store(path,'json'),Store(path,'json')
    def write(store): return store.add_memory_if_absent('same fact','fact',3,'test')[0]
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(write,(a,b)))==['created','reinforced']
    assert len(a.memories())==1
    a.add_memory_if_absent('the rapist','fact',3,'test')
    a.add_memory_if_absent('therapist','fact',3,'test')
    assert len(a.memories())==3
    a.close(); b.close()


def test_reindex_finds_missing_outside_prefix_and_reports_global_remaining(tmp_path):
    class Embed:
        enabled=True; namespace='engineering'; timeout_seconds=1
        async def embed_batch(self,texts): return [[1.0,0.0] for _ in texts]
    store=Store(tmp_path/'index.db','json')
    engine=MemoryEngine(store,Embed())
    for index in range(3): store.add_memory('已有向量'+str(index),importance=5,embedding=[1.0,0.0])
    missing=[store.add_memory('待补向量'+str(index),importance=1) for index in range(3)]
    result=asyncio.run(engine.reindex(limit=2))
    assert result=={'enabled':True,'indexed':2,'remaining':1}
    assert asyncio.run(engine.reindex(limit=2))=={'enabled':True,'indexed':1,'remaining':0}
    assert all(store.memory(m['id'])['embedding'] for m in missing)
    store.close()


def test_failed_backfill_does_not_starve_other_rows_or_hide_remaining(tmp_path):
    class Embed:
        enabled=True; namespace='engineering'; timeout_seconds=1
        async def embed_batch(self,texts):
            if '失败' in texts[0]: raise RuntimeError('engineering failure')
            return [[1.0,0.0] for _ in texts]
    store=Store(tmp_path/'failure.db','json')
    engine=MemoryEngine(store,Embed())
    store.add_memory('失败工程条目')
    good=store.add_memory('成功工程条目')
    assert asyncio.run(engine.reindex(limit=1))['remaining']==2
    assert asyncio.run(engine.reindex(limit=1))=={'enabled':True,'indexed':1,'remaining':1}
    assert store.memory(good['id'])['embedding']
    store.close()


def test_retrieval_does_not_run_document_backfill(tmp_path):
    class Embed:
        enabled=True; namespace='engineering'; timeout_seconds=1
        async def embed(self,text): return [1.0,0.0]
        async def embed_batch(self,texts): raise AssertionError('document maintenance must not block a read')
    store=Store(tmp_path/'read.db','json')
    engine=MemoryEngine(store,Embed())
    memory=store.add_memory('工程冷索引词面')
    assert asyncio.run(engine.retrieve('工程冷索引'))[0]['id']==memory['id']
    assert store.memory(memory['id'])['embedding'] is None
    store.close()


def test_reordered_chinese_bigrams_find_low_priority_memory_outside_window(tmp_path):
    store=Store(tmp_path/'bigram.db','json')
    target=store.add_memory('茶偏好是龙井','preference',1)
    for i in range(105): store.add_memory('无关工程记录'+str(i),importance=5)
    engine=MemoryEngine(store,vector_scan_limit=100)
    for mode in ('keyword','bm25','rrf'):
        assert asyncio.run(engine.retrieve('龙井偏好',mode=mode,expand_graph=False))[0]['id']==target['id']
    old=store.correct_memory(target['id'],'茶偏好是碧螺春','preference',1,'工程纠正')
    assert not asyncio.run(engine.retrieve('龙井',mode='bm25',expand_graph=False))
    assert asyncio.run(engine.retrieve('碧螺春',mode='bm25',expand_graph=False))[0]['id']==old['id']
    store.close()


def test_soft_kind_quotas_fill_relevant_same_type_evidence(tmp_path):
    store=Store(tmp_path/'quota.db','json')
    ids={store.add_memory('工程聚会事实'+str(i),'fact')['id'] for i in range(8)}
    diagnostics={}
    results=asyncio.run(MemoryEngine(store).retrieve('工程聚会',limit=8,expand_graph=False,diagnostics=diagnostics))
    assert {m['id'] for m in results}==ids
    assert diagnostics['quota_policy']=='soft_diversity_then_relevance'
    assert diagnostics['returned_count']==8
    assert len(asyncio.run(MemoryEngine(store).retrieve('工程聚会',limit=3)))==3
    store.close()


def test_bulk_read_batches_metadata_without_stale_cross_request_cache(tmp_path):
    store=Store(tmp_path/'batch.db','json')
    for i in range(250): store.add_memory('批量工程记录'+str(i),embedding=[1.0,0.0])
    statements=[]
    store.db.set_trace_callback(statements.append)
    results=store.memories(limit=250)
    store.db.set_trace_callback(None)
    reads=[q for q in statements if q.lstrip().upper().startswith(('SELECT','WITH'))]
    assert len(reads)==3  # One parent query, one vectors query, one grouped chunks query.
    assert all(m['embedding']==[1.0,0.0] for m in results)
    victim=results[0]
    store.delete_memory(victim['id'])
    assert victim['id'] not in {m['id'] for m in store.memories(limit=250)}
    store.close()


@pytest.mark.parametrize('tool', ['recall_memory','search_history'])
def test_truncated_builtin_output_retains_trusted_freshness_dependency(tmp_path, tool):
    from memoria.tools import build_registry
    store=Store(tmp_path/'truncated.db','json')
    source=store.add_message(store.create_session()['id'],'user','工程截断来源'+('长文'*300))
    memory=store.add_memory(source['content'],source_ref=source['id'],require_source=True)
    registry=build_registry(store,MemoryEngine(store))
    registry.get(tool).max_output_chars=40
    result=asyncio.run(registry.execute(tool,{'query':'工程截断来源'}))
    assert result.ok and '截断' in result.content and result.memory_dependencies
    assert all(store.memory_dependency_valid(d) for d in result.memory_dependencies)
    store.delete_session(source['session_id'])
    assert not any(store.memory_dependency_valid(d) for d in result.memory_dependencies)
    store.close()
