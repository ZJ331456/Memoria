"""Isolated synthetic source retrieval contracts; no paid calls or benchmark gold."""
import asyncio

import pytest

from memoria.memory.embedding import DocumentEmbedding
from memoria.memory.library import SourceInput, RevisionInput
from memoria.memory.records import MemorySearch
from memoria.memory.source_units import render_turns
from memoria.memory.evidence.query_plan import plan
from tests.test_memory_kernel import core

__all__ = ['core']  # Explicitly expose the imported pytest fixture.


class Encoder:
    enabled = True
    is_local = True
    namespace = 'synthetic-source-space'

    def __init__(self):
        self.document_calls = 0
        self.query_calls = 0
        self.after_documents = None
        self.after_query = None

    async def embed_documents(self, texts):
        self.document_calls += 1
        result = [DocumentEmbedding([1.,0.] if 'storage' in t else [0.,1.]) for t in texts]
        if self.after_documents:self.after_documents()
        return result

    async def embed_query(self, text):
        self.query_calls += 1
        if self.after_query:self.after_query()
        return [1.,0.]


def add(core, text='Atlas storage incident.', **fields):
    return core.ingest(SourceInput(kind='document',title='Synthetic source',content=text,**fields))


def run(core, query='database failure', **kw):
    return asyncio.run(core.search(MemorySearch(query=query,domains=['library'],**kw)))


def test_dense_native_route_recalls_lexically_missing_source_and_reuses_cache(core):
    target=add(core);add(core,'Dinner and mountain holidays.')
    encoder=Encoder();core.memory.embedder=encoder
    assert run(core,memory_mode='bm25')['evidence']==[]
    first=run(core,memory_mode='dense')
    assert first['evidence'][0]['source_id']==target['id']
    assert first['retrieval']['library']['dense_available']
    assert first['retrieval']['library']['index_written']==2
    second=run(core,memory_mode='rrf')
    assert second['evidence'][0]['source_id']==target['id']
    assert second['retrieval']['library']['index_written']==0
    assert encoder.document_calls==1 and encoder.query_calls==2
    assert not core.store.memories()


def test_source_scope_filters_before_vectors_and_no_other_session_backfill(core):
    a=core.store.create_session()['id'];b=core.store.create_session()['id']
    target=add(core,scope='working',session_id=a)
    hidden=add(core,scope='working',session_id=b)
    encoder=Encoder();core.memory.embedder=encoder
    result=run(core,session_id=a)
    assert [e['source_id'] for e in result['evidence']]==[target['id']]
    assert result['retrieval']['library']['indexed_passages']==1
    chunks={r[0] for r in core.store.db.execute('SELECT chunk_id FROM library_vectors')}
    hidden_chunks={r[0] for r in core.store.db.execute('SELECT id FROM library_chunks WHERE revision_id=?',(hidden['head_id'],))}
    assert chunks.isdisjoint(hidden_chunks)


@pytest.mark.parametrize('stage',['after_documents','after_query'])
def test_retraction_while_embedding_cannot_return_a_source(core,stage):
    source=add(core);encoder=Encoder();core.memory.embedder=encoder
    setattr(encoder,stage,lambda:core.library.retract(source['id']))
    result=run(core)
    assert result['evidence']==[] and result['dependencies']==[]
    assert core.store.db.execute('SELECT count(*) FROM library_vectors').fetchone()[0]==0


def test_source_revision_purges_vectors_and_never_reuses_old_body(core):
    source=add(core);encoder=Encoder();core.memory.embedder=encoder
    first=run(core)
    assert first['evidence']
    core.library.revise(source['id'],RevisionInput(expected_revision_id=source['head_id'],
        title='Revised source',content='Unrelated food.'))
    assert core.store.db.execute('SELECT count(*) FROM library_vectors').fetchone()[0]==0
    assert not core.dependency_valid(first['dependencies'][0])
    assert run(core,memory_mode='dense')['evidence']==[]


def test_model_switch_during_indexing_discards_the_entire_result(core):
    add(core);encoder=Encoder();core.memory.embedder=encoder
    replacement=Encoder();replacement.namespace='new-synthetic-space'
    encoder.after_documents=lambda:setattr(core.memory,'embedder',replacement)
    result=run(core,memory_mode='rrf')
    assert result['retrieval']['library']['fallback']
    assert not result['retrieval']['library']['dense_available']
    assert core.store.db.execute('SELECT count(*) FROM library_vectors').fetchone()[0]==0


@pytest.mark.parametrize('bad',[[],[0.,0.],[float('nan'),1.],[1.,2.,3.]])
def test_invalid_document_batch_is_atomic_and_fallback_is_honest(core,bad):
    add(core);add(core,'Second storage source.')
    encoder=Encoder();core.memory.embedder=encoder
    async def docs(texts):return [DocumentEmbedding([1.,0.]),DocumentEmbedding(bad)]
    encoder.embed_documents=docs
    result=run(core,query='storage')
    assert result['retrieval']['library']['fallback']
    assert not result['retrieval']['library']['dense_available']
    assert core.store.db.execute('SELECT count(*) FROM library_vectors').fetchone()[0]==0
    assert result['evidence']  # explicit lexical fallback, not a claimed Dense success


def test_chunk_max_not_parent_mean_and_whole_passage_is_cited(core):
    source=add(core,'A storage incident followed a long planning discussion.')
    encoder=Encoder();core.memory.embedder=encoder
    async def docs(texts):
        return [DocumentEmbedding([0.,1.],[{'start':0,'end':20,'embedding':[0.,1.]},
            {'start':18,'end':len(texts[0]),'embedding':[1.,0.]}])]
    encoder.embed_documents=docs
    result=run(core,memory_mode='dense')
    assert result['evidence'][0]['source_id']==source['id']
    assert result['evidence'][0]['content']==source['revision']['content']
    assert len(result['evidence'])==1


def test_remote_bulk_index_never_silently_spends_embedding_requests(core):
    add(core);encoder=Encoder();encoder.is_local=False;core.memory.embedder=encoder
    result=run(core,query='storage')
    assert result['retrieval']['library']['fallback']
    assert result['retrieval']['library']['index_note']=='remote_bulk_index_requires_explicit_bounded_request'
    assert encoder.document_calls==encoder.query_calls==0


def test_adjacency_keeps_separate_raw_speakers_and_never_asserts_a_link(core):
    text,units=render_turns([
        {'id':'e0','speaker':'A','text':'We adopted storage snapshots for Atlas.'},
        {'id':'e1','speaker':'B','text':'Why did you choose that approach?'},
        {'id':'e2','speaker':'A','text':'Because it made recovery straightforward.'}])
    source=add(core,text,source_units=units)
    capture={}
    asyncio.run(core.search(MemorySearch(query='storage',domains=['library'],memory_mode='bm25',limit=1),capture=capture))
    assert len(capture['candidates'])>=2
    assert {e['provenance']['source_unit']['speaker'] for e in capture['candidates']}=={'A','B'}
    assert all(e['source_id']==source['id'] for e in capture['candidates'])
    assert all(not e['semantic_link_verified'] for e in capture['retrieval']['library']['adjacency_hints'])
    assert len({e['citation'] for e in capture['candidates']})==len(capture['candidates'])


def test_frozen_query_plan_requires_both_speakers_and_interval_endpoints():
    shared=plan('Which city have both Alice and Bob visited?',{})
    assert len(shared['requirements'])==3
    assert 'subject_Alice:' in shared['requirements'][0]['fact']
    assert 'subject_Bob:' in shared['requirements'][1]['fact']
    duration=plan('How long did the Atlas migration take?',{})
    assert len(duration['requirements'])==2
    assert all(r['kind']=='fact' for r in duration['requirements'])
    count=plan('How many times did Alice visit the lake?',{})
    assert len(count['requirements'])==2 and count['gold_used'] is False


def test_retention_policy_is_not_misrouted_as_elapsed_event_history():
    from memoria.memory.evidence.query_plan import complex_recall
    for query in ('审计日志保留多久？','How long is the audit retention policy?'):
        assert len(plan(query,{})['requirements'])==1
        assert not complex_recall(query)


def test_morphology_is_a_retrieval_hint_and_preserves_negation_and_chinese():
    from memoria.memory.source_retrieval import terms
    assert terms('opening')==terms('opened')==terms('open')
    assert 'not' in terms('did not open') and 'never' in terms('never opened')
    assert terms('中文检索')


def test_source_scan_overflow_is_disclosed(core,monkeypatch):
    import memoria.memory.source_retrieval as module
    add(core,'Old storage facts.');add(core,'New storage facts.')
    monkeypatch.setattr(module,'SCAN_LIMIT',1)
    result=run(core,query='storage',memory_mode='bm25')
    assert result['retrieval']['library']['scan_truncated']
    assert result['retrieval']['library']['scanned_passages']==1
