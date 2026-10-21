from tests.kernel_factory import tool_kernel
"""Long source coverage and lifecycle consistency across both vector backends."""
import asyncio
from pathlib import Path

import pytest

from memoria.config import ModelConfig
from memoria.memory.embedding import EmbeddingClient
from memoria.memory.engine import MemoryEngine
from memoria.memory.token_chunks import TokenChunker
from memoria.prompting import PromptAssembler
from memoria.observability.timing import StageTimings
from memoria.store import Store
from tests.test_token_chunks import CharacterTokenizer


@pytest.fixture(params=["json", "sqlite-vec"])
def setup_memory(tmp_path, monkeypatch, request):
    monkeypatch.setattr("memoria.memory.token_chunks.local_chunker", lambda *a, **kw: TokenChunker(CharacterTokenizer(), 64, 128))
    client = EmbeddingClient(ModelConfig("bge-small-zh-v1.5", "", "http://127.0.0.1:8080/v1"))
    client.query_vector = [0.0, 1.0, 0.0]

    async def encode(texts):
        return [[0.0, 1.0, 0.0] if "OLDTAIL" in text else [0.0, 0.0, 1.0] if "NEWTAIL" in text else [1.0, 0.0, 0.0] for text in texts]

    async def query(text):
        return client.query_vector

    monkeypatch.setattr(client, "embed_batch", encode)
    monkeypatch.setattr(client, "embed_query", query)
    store = Store(tmp_path / "chunks.db", request.param)
    if request.param == "sqlite-vec" and not store.vector_index_status["enabled"]:
        store.close()
        pytest.skip("optional sqlite-vec unavailable")
    engine = MemoryEngine(store, client)
    yield store, engine, client
    store.close()


def long_text(tail="OLDTAIL"):
    return "生活记录：今天散步看书。" * 40 + "\n" + tail


def test_tail_is_semantic_hit_and_survives_prompt_budget(setup_memory):
    store, engine, _ = setup_memory
    text = long_text()
    timers = StageTimings()
    item = asyncio.run(engine.remember(text, "fact", 3, "manual", timings=timers)).memory
    assert timers.stages["embedding.chunking"]["calls"] == 1
    assert timers.stages["embedding.documents"]["calls"] == 1
    chunks = store.memory_chunks(item["id"])
    assert len(chunks) > 1 and chunks[-1]["end"] == len(text)
    assert sum(c["end"] - c["start"] for c in chunks) == len(text)
    hits = asyncio.run(engine.retrieve("Ω", expand_graph=False))
    assert [h["id"] for h in hits] == [item["id"]]
    assert hits[0]["retrieval"]["vector_similarity"] == 1
    assert hits[0]["retrieval"]["chunk"]["start"] > 0
    assert "OLDTAIL" in hits[0]["retrieval_excerpt"]
    frame = PromptAssembler(max_frame_chars=900).assemble("system", memories=hits).context_frame["content"]
    assert "OLDTAIL" in frame and item["id"] in frame


def test_correction_time_travel_and_deletion_are_parent_atomic(setup_memory):
    store, engine, client = setup_memory
    old = asyncio.run(engine.remember(long_text(), "fact", 3, "manual")).memory
    new = asyncio.run(engine.correct(old["id"], long_text("NEWTAIL"), "fact", 3, "纠正尾部"))
    assert asyncio.run(engine.retrieve("Ω", expand_graph=False)) == []
    historical = asyncio.run(engine.retrieve("Ω", as_of=old["created_at"], expand_graph=False))
    assert historical[0]["id"] == old["id"] and "OLDTAIL" in historical[0]["retrieval_excerpt"]
    client.query_vector = [0.0, 0.0, 1.0]
    assert asyncio.run(engine.retrieve("Ω", expand_graph=False))[0]["id"] == new["id"]
    assert store.delete_memory(new["id"])
    assert store.memory_chunks(new["id"]) == []
    assert asyncio.run(engine.retrieve("Ω", expand_graph=False)) == []


def test_withdraw_restores_previous_chunks_and_restart(setup_memory):
    store, engine, _ = setup_memory
    old = asyncio.run(engine.remember(long_text(), "fact", 3, "manual", "source-old")).memory
    doc = asyncio.run(engine.embedder.embed_documents([long_text("NEWTAIL")]))[0]
    new = store.add_memory(long_text("NEWTAIL"), "fact", embedding=doc.vector,
        embedding_chunks=doc.chunks, supersedes_id=old["id"], source_ref="source-new")
    store.undo_memory_sources(["source-new"])
    assert store.memory(new["id"])["status"] != "active"
    assert asyncio.run(engine.retrieve("Ω", expand_graph=False))[0]["id"] == old["id"]
    path = store.db.execute("PRAGMA database_list").fetchone()[2]
    reopened = Store(Path(path), store.vector_backend)
    try:
        again = MemoryEngine(reopened, engine.embedder)
        assert asyncio.run(again.retrieve("Ω", expand_graph=False))[0]["id"] == old["id"]
    finally:
        reopened.close()


def test_chunk_write_failure_rolls_back_parent_vectors_and_intervals(setup_memory):
    store, engine, _ = setup_memory
    store.db.execute("CREATE TEMP TRIGGER fail_chunk BEFORE INSERT ON memory_embedding_chunks BEGIN SELECT RAISE(ABORT, 'chunk failure'); END")
    with pytest.raises(Exception, match="chunk failure"):
        asyncio.run(engine.remember(long_text(), "fact", 3, "manual"))
    for table in ("memories", "memory_embeddings", "memory_embedding_chunks", "memory_validity_intervals"):
        assert store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_legacy_truncated_vector_reindexed_and_stale_backfill_rejected(setup_memory):
    store, engine, _ = setup_memory
    old = store.add_memory(long_text(), "fact", embedding=[1.0, 0.0, 0.0])
    report = asyncio.run(engine.reindex())
    assert report["indexed"] == 1 and report["remaining"] == 0
    assert asyncio.run(engine.retrieve("Ω", expand_graph=False))[0]["id"] == old["id"]
    doc = asyncio.run(engine.embedder.embed_documents([old["content"]]))[0]
    store.correct_memory(old["id"], "新事实", "fact", 3, "并发更正")
    assert not store.set_memory_document_embedding(old["id"], old["content"], doc.vector, doc.chunks)


def test_namespaces_isolate_chunks_and_keep_original_space(setup_memory):
    store, engine, _ = setup_memory
    item = asyncio.run(engine.remember(long_text(), "fact", 3, "manual")).memory
    namespace = store.embedding_namespace
    store.configure_embedding("other-model")
    assert store.memory_chunks(item["id"]) == []
    assert store.chunk_memory_candidates([0.0, 1.0, 0.0]) == []
    store.configure_embedding(namespace)
    assert store.chunk_memory_candidates([0.0, 1.0, 0.0])[0][0]["id"] == item["id"]


def test_many_chunks_of_one_parent_do_not_hide_next_parent(setup_memory):
    store, engine, _ = setup_memory
    first = asyncio.run(engine.remember("OLDTAIL" * 200, "fact", 3, "manual")).memory
    second = store.add_memory("备选记忆", "fact", embedding=[0, .8, .6],
        embedding_chunks=[{"start": 0, "end": 4, "tokens": 6, "embedding": [0, .8, .6]}])
    hits = store.chunk_memory_candidates([0, 1, 0], limit=2)
    assert [h[0]["id"] for h in hits] == [first["id"], second["id"]]


def test_invalid_chunk_dimensions_are_atomic_and_metadata_edit_retains_index(setup_memory):
    store, engine, _ = setup_memory
    with pytest.raises(ValueError, match="dimensions differ"):
        store.add_memory("完整原文", embedding=[1, 0, 0],
            embedding_chunks=[{"start": 0, "end": 4, "tokens": 6, "embedding": [1, 0]}])
    assert store.memories() == []
    saved = asyncio.run(engine.remember(long_text(), "fact", 3, "manual")).memory
    updated = store.update_memory(saved["id"], {"importance": 5})
    assert updated["embedding_chunk_count"] == saved["embedding_chunk_count"] > 1
    assert asyncio.run(engine.retrieve("Ω", expand_graph=False))[0]["id"] == updated["id"]


def test_complementary_matched_chunks_reach_prompt_without_extra_parent_votes(setup_memory):
    store, engine, _ = setup_memory
    parts = ["Engineering alpha. ", "Engineering beta. ", "Engineering gamma. ", "Engineering delta. "]
    parts[-1] = parts[-1].rstrip()
    text, start, chunks = "".join(parts), 0, []
    for part in parts:
        end = start+len(part)
        chunks.append(dict(start=start,end=end,tokens=len(part)+2,embedding=[0.,1.,0.]))
        start = end
    parent = store.add_memory(text, embedding=[0.,1.,0.],embedding_chunks=chunks)
    hits = asyncio.run(engine.retrieve("Ω", limit=1, expand_graph=False))
    assert [h["id"] for h in hits] == [parent["id"]]
    assert len(hits[0]["retrieval_excerpts"]) == 3
    assert hits[0]["retrieval"]["vector_rank"] == 1
    for part in hits[0]["retrieval_excerpts"]:
        assert part["text"] == text[part["start"]:part["end"]]
    frame = PromptAssembler(max_frame_chars=6000).assemble("system",memories=hits).context_frame["content"]
    assert all(word in frame for word in ("alpha","beta","gamma"))
    assert "delta" not in frame and frame.count(parent["id"]) == 3
    from memoria.tools import build_registry
    result = asyncio.run(build_registry(tool_kernel(store, engine)).execute("recall_memory", {"query":"Ω", "limit":1}))
    assert result.ok and all(word in result.content for word in ("alpha","beta","gamma"))
    assert len(result.memory_dependencies) == 1


def test_changed_parent_never_reuses_old_chunk_coordinates(setup_memory):
    store, _, _ = setup_memory
    parent = store.add_memory("Engineering original",embedding=[0.,1.,0.],
        embedding_chunks=[dict(start=0,end=20,tokens=22,embedding=[0.,1.,0.])])
    with store.db:
        store.db.execute("UPDATE memories SET content='Engineering changed!' WHERE id=?",(parent["id"],))
    assert not store.chunk_memory_candidates([0.,1.,0.])
