"""Engineering encoder doubles verify index/filter contracts, not retrieval quality."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from memoria.claims import ClaimMemory
from memoria.memory import MemoryEngine
from memoria.memory.access import MemoryAccessOrchestrator, QueryRequest
from memoria.memory.embedding import DocumentEmbedding, EmbeddingError
from memoria.memory.experience import dense_candidates, query, reindex_dense
from memoria.store import Store


class Encoder:
    enabled=True;is_local=True;namespace='engineering-encoder-v1'
    async def embed_documents(self,texts):return [DocumentEmbedding([1.,0.]) for text in texts]
    async def embed_query(self,text):return [1.,0.]


@pytest.fixture
def core(tmp_path):
    store=Store(tmp_path/'dense.db','json');claims=ClaimMemory(store,'engineering-owner')
    sid=store.create_session('Engineering source')['id'];encoder=Encoder()
    MemoryEngine(store,encoder)
    yield store,claims,sid,encoder
    store.close()


def add(core,text):return core[0].add_message(core[2],'user',text)


def test_dense_retrieves_nonlexical_source_and_rrf_uses_one_vote_per_lane(core):
    msg=add(core,'Engineering commute by car.')
    assert asyncio.run(reindex_dense(core[0],core[3]))['indexed_chunks']==1
    dense,diag=asyncio.run(query(core[1],'automobile travel',core[3],mode='dense'))
    assert dense[0]['source_ids']==[msg['id']] and diag['bm25_candidates']==0
    assert diag['dense']['namespace']==core[3].namespace and diag['model_calls']==1
    fused,diag=asyncio.run(query(core[1],'car',core[3],mode='rrf'))
    assert len(fused)==1 and fused[0]['data']['retrieval']['lanes']==['bm25','dense']
    assert diag['bm25_candidates']==diag['dense_candidates']==1


def test_future_public_and_revoked_vectors_filtered_before_top_one(core):
    old=add(core,'Engineering valid earlier source.')
    asyncio.run(reindex_dense(core[0],core[3]))
    new=add(core,'Engineering future source.')
    asyncio.run(reindex_dense(core[0],core[3]))
    public=add(core,'Engineering public source.')
    asyncio.run(reindex_dense(core[0],core[3]))
    with core[0]._memory_transaction():
        core[0].db.execute('INSERT INTO dataset_imports VALUES(?,?,?,?,?)',('public','sha','{}',
            json.dumps({'source_mapping':[{'message_ids':[public['id']]}]}),'now'))
    found,diag=dense_candidates(core[0],[1.,0.],core[3].namespace,limit=1,recorded_as_of=old['created_at'])
    assert found[0]['message_id']==old['id'] and diag['scanned_vectors']==1
    core[0].undo_memory_sources([old['id'],new['id']])
    assert not dense_candidates(core[0],[1.,0.],core[3].namespace)[0]


@pytest.mark.parametrize('mutation',['edit','delete','namespace'])
def test_dense_maintenance_rechecks_source_and_namespace_after_await(core,mutation):
    src=add(core,'Engineering original source.')
    class Racing(Encoder):
        async def embed_documents(self,texts):
            if mutation=='namespace':core[0].configure_embedding('other-encoder')
            else:
                with core[0]._memory_transaction():
                    if mutation=='delete':core[0].db.execute('DELETE FROM messages WHERE id=?',(src['id'],))
                    else:core[0].db.execute("UPDATE messages SET content='changed' WHERE id=?",(src['id'],))
            return [DocumentEmbedding([1.,0.]) for text in texts]
    result=asyncio.run(reindex_dense(core[0],Racing()))
    assert result['indexed_chunks']==0 and not core[0].db.execute('SELECT 1 FROM experience_vectors').fetchone()


def test_invalid_or_incomplete_embedding_batches_never_partially_commit(core):
    add(core,'Engineering one.');add(core,'Engineering two.')
    class Invalid(Encoder):
        async def embed_documents(self,texts):return [DocumentEmbedding([float('nan'),1.]) for text in texts]
    with pytest.raises(ValueError):asyncio.run(reindex_dense(core[0],Invalid()))
    assert not core[0].db.execute('SELECT 1 FROM experience_vectors').fetchone()


def test_dense_no_encoder_never_disguises_lexical_results_and_rrf_marks_degradation(core):
    add(core,'Engineering cedar source.')
    found,diag=asyncio.run(query(core[1],'cedar',None,mode='dense'))
    assert not found and not diag['dense']['available'] and diag['model_calls']==0
    found,diag=asyncio.run(query(core[1],'cedar',None,mode='rrf'))
    assert found and not diag['dense']['available'] and diag['dense_candidates']==0


def test_dense_query_errors_are_visible_with_attempted_call_count(core):
    class Broken(Encoder):
        async def embed_query(self,text):raise EmbeddingError('private provider detail')
    found,diag=asyncio.run(query(core[1],'cedar',Broken(),mode='dense'))
    assert not found and diag['model_calls']==1 and diag['dense']['reason']=='query_embedding_failed'
    assert 'private provider detail' not in json.dumps(diag)


def test_changed_sources_after_query_encoding_do_not_enter_access_packet(core):
    src=add(core,'Engineering cedar source.')
    asyncio.run(reindex_dense(core[0],core[3]))
    class Racing(Encoder):
        async def embed_query(self,text):
            core[0].undo_memory_sources([src['id']]);return [1.,0.]
    req=QueryRequest(query='cedar',recorded_as_of=src['created_at'],experience_mode='dense')
    packet=asyncio.run(MemoryAccessOrchestrator(core[1],MemoryEngine(core[0],Racing())).access(req))
    assert not packet['candidates'] and packet['path']=='clarify'
    assert 'source' in packet['production_trace']['changed_stages']


def test_model_namespaces_and_dimensions_never_mix(core):
    add(core,'Engineering cedar source.');asyncio.run(reindex_dense(core[0],core[3]))
    assert not dense_candidates(core[0],[1.,0.],'other-encoder')[0]
    assert not dense_candidates(core[0],[1.,0.,0.],core[3].namespace)[0]


def test_query_model_switch_during_await_rejects_old_space(core):
    add(core,'Engineering original.');asyncio.run(reindex_dense(core[0],core[3]))
    class Switch(Encoder):
        async def embed_query(self,text):
            core[0].configure_embedding('other-encoder');return [1.,0.]
    found,diag=asyncio.run(query(core[1],'cedar',Switch(),mode='dense'))
    assert not found and diag['dense']['reason']=='embedding_namespace_changed'


def test_only_process_or_admin_loopback_bge_is_accepted_for_maintenance():
    from memoria.config import ModelConfig
    from memoria.memory.embedding import EmbeddingClient
    from memoria.memory.experience import local_encoder
    assert local_encoder(EmbeddingClient(ModelConfig('bge-small-zh-v1.5','','http://127.0.0.1:8080/v1')))
    assert not local_encoder(EmbeddingClient(ModelConfig('bge-small-zh-v1.5','placeholder','https://example.test/v1')))
    assert not local_encoder(EmbeddingClient(ModelConfig('other-model','','http://127.0.0.1:8080/v1')))


def test_mixed_dimension_batch_is_atomic_and_remains_backfillable(core):
    add(core,'Engineering first source.');add(core,'Engineering second source.')
    class Mixed(Encoder):
        async def embed_documents(self,texts):return [DocumentEmbedding([1.,0.]),DocumentEmbedding([1.,0.,0.])]
    with pytest.raises(ValueError,match='mixed dimensions'):asyncio.run(reindex_dense(core[0],Mixed()))
    assert not core[0].db.execute('SELECT 1 FROM experience_vectors').fetchone()
    assert asyncio.run(reindex_dense(core[0],core[3]))['indexed_chunks']==2


def test_same_namespace_dimension_drift_does_not_poison_remaining_cache(core):
    add(core,'Engineering first source.');asyncio.run(reindex_dense(core[0],core[3]))
    add(core,'Engineering new source.')
    class Drift(Encoder):
        async def embed_documents(self,texts):return [DocumentEmbedding([1.,0.,0.]) for text in texts]
    with pytest.raises(ValueError,match='new model namespace'):asyncio.run(reindex_dense(core[0],Drift()))
    assert core[0].db.execute('SELECT COUNT(*) FROM experience_vectors').fetchone()[0]==1
    assert asyncio.run(reindex_dense(core[0],core[3]))['indexed_chunks']==1
