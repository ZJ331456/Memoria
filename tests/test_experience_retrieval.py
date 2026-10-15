"""Temporary engineering examples only, not personal or research histories."""
import asyncio
import json

import pytest

from memoria.claims import ClaimMemory, canonical
from memoria.memory import MemoryEngine
from memoria.memory.access import MemoryAccessOrchestrator, QueryRequest
from memoria.memory.experience import candidates, reindex, retrieve
from memoria.memory.reader import CitationGuard
from memoria.store import Store


@pytest.fixture
def core(tmp_path):
    store = Store(tmp_path / "experiences.db", "json")
    claims = ClaimMemory(store, "engineering-owner")
    sid = store.create_session("Temporary experience regression")["id"]
    yield store, claims, sid
    store.close()


def add(core, text, role="user"):
    return core[0].add_message(core[2], role, text)


def access(core, query, **kwargs):
    request = QueryRequest(query=query, **kwargs)
    result = asyncio.run(MemoryAccessOrchestrator(core[1], MemoryEngine(core[0])).access(request))
    return request, result


def test_english_spaces_and_chinese_tokens_retrieve_original_chunks(core):
    english = add(core, "Engineering weekend experience: visited an art gallery with friends.")
    chinese = add(core, "工程临时输入：周末参观天文博物馆。")
    assert candidates(core[0], "Which art gallery?")[0]["message_id"] == english["id"]
    assert candidates(core[0], "天文博物馆")[0]["message_id"] == chinese["id"]
    request, result = access(core, "art gallery")
    assert result["selection"]["payload"]["evidence"]
    guard = CitationGuard(core[1], result, request)
    assert guard.catalog and guard.freshness() is None
    assert all(not c["data"]["approved_personal_state"] for c in result["candidates"])
    assert not core[1].list()


def test_cutoff_role_revocation_and_public_filter_happen_before_limit(core):
    old = add(core, "Engineering cedar observation.")
    cutoff = old["created_at"]
    future = add(core, "Engineering cedar cedar cedar cedar future observation.")
    assistant = add(core, "cedar "*50, "assistant")
    public = add(core, "cedar "*50)
    with core[0].db:
        core[0].db.execute("INSERT INTO dataset_imports VALUES(?,?,?,?,?)", ("temporary-public", "sha", "{}",
            json.dumps({"source_mapping":[{"message_ids":[public["id"]]}]}), "now"))
    found, diag = retrieve(core[1], "cedar", limit=1, recorded_as_of=cutoff)
    assert len(found) == 1 and found[0]["source_ids"] == [old["id"]]
    assert diag["anchors"] == 1 and diag["recorded_as_of"] == cutoff
    assert future["id"] not in canonical(found) and assistant["id"] not in canonical(found)
    core[0].undo_memory_sources([old["id"], future["id"]])
    assert not candidates(core[0], "cedar", limit=1)


def test_long_source_has_complementary_chunks_and_bounded_local_expansion(core):
    source = add(core, "begin alpha. "+"engineering filler. "*110+" tail omega.")
    result, diag = retrieve(core[1], "alpha omega", limit=8)
    spans = [c["data"]["evidence"] for c in result]
    assert len(spans) >= 2 and diag["expanded_chunks"] <= 8
    assert any("alpha" in e["quote"] for e in spans) and any("omega" in e["quote"] for e in spans)
    assert all(e["quote"] == source["content"][e["start"]:e["end"]] for e in spans)
    request, packet = access(core, "alpha omega", budget_chars=16000)
    guard = CitationGuard(core[1], packet, request)
    assert len(guard.catalog) >= 2  # Span coordinates prevent citation collisions.
    assert len(guard.catalog) == len(set(guard.catalog)) and guard.freshness() is None


@pytest.mark.parametrize("mutation", ["edit", "delete", "revoke", "date", "session"])
def test_index_and_inflight_reader_cannot_publish_stale_source(core, mutation):
    source = add(core, "Engineering cedar original.")
    request, result = access(core, "cedar")
    guard = CitationGuard(core[1], result, request)
    assert guard.catalog and guard.freshness() is None
    if mutation == "revoke":
        core[0].undo_memory_sources([source["id"]])
    else:
        with core[0].db:
            if mutation == "delete":
                core[0].db.execute("DELETE FROM messages WHERE id=?", (source["id"],))
            elif mutation == "edit":
                core[0].db.execute("UPDATE messages SET content='Engineering changed text' WHERE id=?", (source["id"],))
            elif mutation == "date":
                core[0].db.execute("UPDATE messages SET created_at='2099-01-01T00:00:00+00:00' WHERE id=?", (source["id"],))
            else:
                sid = core[0].create_session("Other engineering session")["id"]
                core[0].db.execute("UPDATE messages SET session_id=? WHERE id=?", (sid,source["id"]))
    assert guard.freshness() is not None
    assert not candidates(core[0], "cedar")
    if mutation == "edit":
        assert reindex(core[0])["indexed_sources"] == 1
        assert candidates(core[0], "changed") and not candidates(core[0], "cedar")


def test_source_write_and_index_commit_or_rollback_together(core):
    core[0].db.execute("""CREATE TEMP TRIGGER fail_experience BEFORE INSERT ON experience_terms
        BEGIN SELECT RAISE(ABORT,'injected experience failure'); END""")
    with pytest.raises(Exception, match="injected experience failure"):
        add(core, "Engineering cedar rollback.")
    assert not core[0].db.execute("SELECT 1 FROM messages").fetchone()
    assert not core[0].db.execute("SELECT 1 FROM experience_chunks").fetchone()


def test_bounded_backfill_and_reopen_migration_preserve_source_history(core, tmp_path):
    for i in range(36):
        add(core, f"Engineering cedar {i}.")
    with core[0].db:
        core[0].db.execute("DELETE FROM experience_chunks")
        core[0].db.execute("DELETE FROM experience_versions")
    before = [dict(r) for r in core[0].db.execute("SELECT * FROM messages ORDER BY id")]
    other = Store(tmp_path / "experiences.db", "json")
    try:
        assert other.db.execute("SELECT COUNT(*) FROM experience_versions").fetchone()[0] == 32
        assert reindex(other, 2)["remaining_sources"] == 2
        assert reindex(other, 2)["remaining_sources"] == 0
        assert [dict(r) for r in other.db.execute("SELECT * FROM messages ORDER BY id")] == before
    finally:
        other.close()


def test_source_evidence_does_not_fill_claim_missing_or_fact_time_and_can_be_disabled(core):
    add(core, "工程输入：我住在南京。")
    _, result = access(core, "我现在住在哪里？")
    assert result["path"] == "clarify" and not result["state"]["payload"]["complete"]
    assert result["retrieval"]["experiences"]["anchors"]
    _, disabled = access(core, "南京", include_experiences=False)
    assert not disabled["candidates"]
    _, past = access(core, "南京", fact_as_of="2025-01-01T00:00:00Z")
    assert not past["candidates"]


def test_two_account_stores_do_not_share_original_experiences(core, tmp_path):
    add(core, "Engineering private cedar.")
    other = Store(tmp_path / "other-user.db", "json")
    try:
        assert not candidates(other, "cedar")
    finally:
        other.close()


def test_future_rows_and_backlog_do_not_leak_past_cutoff(core):
    old = add(core, "Engineering cedar earlier.")
    new = add(core, "Engineering cedar future.")
    with core[0].db:
        core[0].db.execute("UPDATE messages SET created_at='2099-01-01T00:00:00+00:00' WHERE id=?", (new["id"],))
    result, diag = retrieve(core[1], "cedar", recorded_as_of=old["created_at"])
    assert {c["source_ids"][0] for c in result} == {old["id"]}
    assert diag["backlog_sources"] == 0
    reindex(core[0])
    assert {c["message_id"] for c in candidates(core[0], "cedar")} == {old["id"]}


def test_continuously_busy_jobs_do_not_starve_index_maintenance(core, monkeypatch):
    from types import SimpleNamespace
    from memoria.memory.worker import MemoryJobWorker
    import memoria.memory.worker as worker_module
    calls = []
    async def index(**kwargs):
        calls.append("index")
    worker = MemoryJobWorker(core[0], None, SimpleNamespace(reindex=index))
    ticks = [0]
    async def busy():
        ticks[0] += 20
        if ticks[0] > 40:
            raise asyncio.CancelledError
        return True
    worker.process_once = busy
    monkeypatch.setattr(worker_module.time, "monotonic", lambda:ticks[0])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.run())
    assert calls == ["index", "index"]
