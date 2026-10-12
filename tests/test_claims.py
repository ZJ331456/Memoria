"""Temporary engineering inputs only: never research users, histories or scores."""
import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event

import pytest
from pydantic import ValidationError

from memoria.claims import (AccessRequest, ClaimDecision, ClaimDraft, ClaimError,
                            ClaimMemory, ClaimRevision, ExtractionRequest, ViewRequest, canonical, infer_access, sha)
from memoria.store import Store
from memoria.tools import build_registry


@pytest.fixture
def core(tmp_path):
    store = Store(tmp_path/"claims.db",vector_backend="json")
    memory = ClaimMemory(store,"engineering-owner")
    session = store.create_session("Temporary engineering case")
    yield store,memory,session["id"]
    store.close()


def evidence(store,sid,text,role="user"):
    message = store.add_message(sid,role,text)
    return {"message_id":message["id"],"start":0,"end":len(text),"quote":text,"content_sha256":sha(text)}


def create(core,value="中文",predicate="response_language",scope=None,**kwargs):
    store,memory,sid=core
    source=evidence(store,sid,"Engineering statement: "+value)
    return memory.create(ClaimDraft(predicate=predicate,value=value,evidence=[source],scope=scope or {},**kwargs))


def approve(core,candidate):
    return core[1].approve(candidate["id"],ClaimDecision(expected_revision=candidate["revision"],reason="工程用例明确批准"))


def request(predicate="response_language",**kwargs):
    return AccessRequest(predicates=[predicate],**kwargs)


def values(result):
    return [claim["value"] for claim in result["payload"]["claims"]]


def revise(core,current,value,action="correct",**kwargs):
    store,memory,sid=core
    source=evidence(store,sid,"Engineering revision: "+value)
    body={k:current[k] for k in ("polarity","modality","fact_start","fact_end","time_precision","memory_id")}
    body.update(kwargs)
    return memory.revise(current["id"],ClaimRevision(expected_revision=current["revision"],action=action,
                         value=value,evidence=[source],**body))


def test_pending_is_not_state_approval_is_explicit_and_source_backed(core):
    candidate=create(core)
    assert candidate["status"]=="pending" and candidate["user_id"]=="engineering-owner"
    assert core[1].access(request())["path"]=="clarify"
    item=approve(core,candidate)
    result=core[1].access(request())
    assert result["path"]=="retrieve" and values(result)==["中文"]
    assert item["verification_status"]=="user_approved" and not item["semantic_support_verified"]
    assert item["evidence"][0]["quote"]=="Engineering statement: 中文"
    assert core[0].overview()["memories"]==0  # Never erase scope into ordinary memory.


def test_approval_rejects_changed_parent_snapshot(core):
    store,memory,sid = core
    parent = store.add_memory("Engineering original parent")
    candidate = create(core,memory_id=parent["id"])
    with store.db:
        store.db.execute("UPDATE memories SET content=? WHERE id=?",("Engineering changed parent",parent["id"]))
    with pytest.raises(ClaimError,match="正文已变化"):
        approve(core,candidate)
    assert memory.get(candidate["id"])["status"] == "pending"
    assert memory.access(request())["path"] == "clarify"


@pytest.mark.parametrize("read",["get","list","history"])
def test_source_reads_hold_consistent_cross_connection_snapshot(core,monkeypatch,read,tmp_path):
    store,memory,sid = core
    current = approve(core,create(core))
    mid = current["evidence"][0]["message_id"]
    other = Store(tmp_path/"claims.db",vector_backend="json")
    attempted = Event()
    original_valid = memory._valid
    pending = []
    def mutate():
        attempted.set()
        with other.db:
            other.db.execute("UPDATE messages SET content=? WHERE id=?",("Engineering changed source",mid))
    with ThreadPoolExecutor(max_workers=1) as pool:
        def validate(data,**kwargs):
            valid = original_valid(data,**kwargs)
            if not pending:
                pending.append(pool.submit(mutate))
                assert attempted.wait(timeout=2)
                with pytest.raises(TimeoutError):
                    pending[0].result(timeout=.1)
            return valid
        monkeypatch.setattr(memory,"_valid",validate)
        result = getattr(memory,read)(current["id"]) if read!="list" else memory.list()
        items = result if isinstance(result,list) else [result]
        assert all(item["evidence"][0]["quote"] == "Engineering statement: 中文" for item in items)
        pending[0].result(timeout=2)
    assert memory.get(current["id"])["source_available"] is False
    other.close()


@pytest.mark.parametrize("mutation",["hash","quote","range","assistant","unknown","public"])
def test_reject_unavailable_forged_or_public_source(core,mutation):
    store,memory,sid=core
    e=evidence(store,sid,"Engineering original",role="assistant" if mutation=="assistant" else "user")
    if mutation=="hash":e["content_sha256"]="0"*64
    if mutation=="quote":e["quote"]="changed"
    if mutation=="range":e["end"]+=1
    if mutation=="unknown":e["message_id"]="missing"
    if mutation=="public":
        with store.db:
            store.db.execute("INSERT INTO dataset_imports VALUES(?,?,?,?,?)",("engineering-public", "sha", "{}",
                json.dumps({"source_mapping":[{"message_ids":[e["message_id"]]}]}),"now"))
    with pytest.raises(ClaimError):memory.create(ClaimDraft(predicate="residence",value="test",evidence=[e]))
    assert memory.list()==[]


@pytest.mark.parametrize("fields",[
    {"predicate":"unknown"},{"user_id":"other"},{"subject_id":"another-user"},
    {"scope":{"travel":{"nested":True}}},{"scope":{"travel":None}},
    {"fact_start":"2024-01-01","time_precision":"day"},
    {"fact_start":"2024-01-01T00:00:00Z"},
    {"fact_start":"2024-02-01T00:00:00Z","fact_end":"2024-01-01T00:00:00Z","time_precision":"exact"},
    {"time_precision":"day"},{"value":"   "},
])
def test_strict_slot_scope_owner_and_time_validation(core,fields):
    store,memory,sid=core
    body={"predicate":"residence","value":"test","evidence":[evidence(store,sid,"Engineering source")]}
    with pytest.raises(ValidationError):ClaimDraft.model_validate({**body,**fields})


def test_scope_exception_typed_conditions_and_missing_context(core):
    approve(core,create(core))
    approve(core,create(core,"English",scope={"travel":True}))
    missing=core[1].access(request())
    assert missing["path"]=="clarify" and not values(missing)
    assert missing["payload"]["missing"][0]["reason"]=="condition_context_missing"
    assert values(core[1].access(request(context={"travel":False})))==["中文"]
    assert values(core[1].access(request(context={"travel":True})))==["English"]
    assert values(core[1].access(request(context={"travel":1})))==["中文"]  # int is not bool.
    assert len(core[1].list())==2


def test_single_slot_requires_explicit_revision_multi_slot_values_coexist(core):
    approve(core,create(core))
    other=create(core,"English")
    with pytest.raises(ClaimError,match="同槽位"):
        approve(core,other)
    approve(core,create(core,"Python",predicate="hobby"))
    approve(core,create(core,"Rust",predicate="hobby"))
    assert set(values(core[1].access(request("hobby"))))=={"Python","Rust"}


def test_equal_specificity_conflicts_are_not_arbitrarily_resolved(core):
    approve(core,create(core,"中文",scope={"travel":True}))
    approve(core,create(core,"English",scope={"work":True}))
    result=core[1].access(request(context={"travel":True,"work":True}))
    assert result["path"]=="clarify" and not values(result)
    assert result["payload"]["missing"][0]["reason"]=="conflicting_assertions"


def test_multivalue_condition_keeps_unrelated_global_values_and_scoped_negation(core):
    approve(core,create(core,"Python",predicate="hobby"))
    approve(core,create(core,"Rust",predicate="hobby"))
    approve(core,create(core,"Hiking",predicate="hobby",scope={"travel":True}))
    approve(core,create(core,"Rust",predicate="hobby",scope={"travel":True},polarity="negative"))
    result=core[1].access(request("hobby",context={"travel":True}))
    states={(item["value"],item["polarity"]) for item in result["payload"]["claims"]}
    assert states=={("Python","positive"),("Rust","negative"),("Hiking","positive")}
    assert result["payload"]["complete"]


def test_opposite_multivalue_assertions_at_same_scope_require_clarification(core):
    approve(core,create(core,"nuts",predicate="diet"))
    approve(core,create(core,"nuts",predicate="diet",polarity="negative"))
    result=core[1].access(request("diet"))
    assert result["path"]=="clarify" and not values(result)


def test_view_invalidation_includes_absence_but_not_unrelated_slots(core):
    store,memory,sid=core
    view=memory.compile(ViewRequest(predicates=["response_language"]))
    assert view["valid"] and not view["payload"]["complete"]
    candidate=create(core)
    assert memory.view(view["id"])["valid"]  # A draft changes no approved state.
    approved=approve(core,candidate)
    assert not memory.view(view["id"])["valid"]
    view=memory.compile(ViewRequest(predicates=["response_language"]))
    assert memory.access(request())["path"]=="view"
    approve(core,create(core,"Python",predicate="hobby"))
    assert memory.view(view["id"])["valid"]
    changed=approve(core,revise(core,approved,"English"))
    assert not memory.view(view["id"])["valid"]
    assert values(memory.access(request()))==["English"]
    assert changed["revision"]==4


def test_correction_is_bitemporal_unknown_fact_time_remains_unknown(core):
    memory=core[1]
    first=approve(core,create(core,"A",predicate="residence",fact_start="2023-01-01T00:00:00Z",time_precision="day"))
    second=approve(core,revise(core,first,"B"))
    assert values(memory.access(request("residence",fact_as_of="2024-01-01T00:00:00Z")))==["B"]
    old=memory.access(request("residence",fact_as_of="2024-01-01T00:00:00Z",recorded_as_of=first["recorded_at"]))
    assert old["path"]=="history" and values(old)==["A"]
    assert memory.access(request("residence",recorded_as_of=memory.history(first["id"])[0]["recorded_at"]))["path"]=="clarify"
    timeline=memory.history(first["id"])
    assert timeline[0]["retired_at"]==timeline[1]["recorded_at"]
    unknown=approve(core,create(core))
    assert unknown["fact_start"] is None
    assert memory.access(request(fact_as_of="2024-01-01T00:00:00Z"))["path"]=="clarify"


def test_transition_preserves_old_fact_window_and_then_allows_correction(core):
    memory=core[1]
    first=approve(core,create(core,"A",predicate="residence",fact_start="2023-01-01T00:00:00Z",time_precision="day"))
    next_item=approve(core,revise(core,first,"B",action="transition",fact_start="2024-01-01T00:00:00Z"))
    assert first["id"]!=next_item["id"]
    assert memory.get(first["id"])["recorded_at"]==next_item["recorded_at"]
    assert values(memory.access(request("residence",fact_as_of="2023-12-31T23:59:59Z")))==["A"]
    assert values(memory.access(request("residence",fact_as_of="2024-01-01T00:00:00Z")))==["B"]
    assert values(memory.access(request("residence",fact_as_of="2025-01-01T00:00:00Z",recorded_as_of=first["recorded_at"])))==["A"]
    revised=approve(core,revise(core,next_item,"C"))
    assert values(memory.access(request("residence")))==["C"]
    assert revised["revision"]==4


def test_future_start_and_half_open_expiry_invalidate_view_without_writes(core,monkeypatch):
    import memoria.claims as module
    clock=datetime.now(timezone.utc)
    later=(clock+timedelta(seconds=20)).isoformat(timespec="microseconds")
    memory=core[1]
    first=approve(core,create(core,"A",predicate="residence",fact_start="2023-01-01T00:00:00Z",time_precision="exact"))
    approve(core,revise(core,first,"B",action="transition",fact_start=later))
    view=memory.compile(ViewRequest(predicates=["residence"],ttl_seconds=600))
    assert view["expires_at"]==later and values({"payload":view["payload"]})==["A"]
    monkeypatch.setattr(module,"now",lambda:later)
    assert not memory.view(view["id"])["valid"]
    assert values(memory.access(request("residence")))==["B"]


def test_rejected_revision_retains_approved_value_and_restore_is_explicit(core):
    memory=core[1]
    first=approve(core,create(core))
    draft=revise(core,first,"English")
    assert values(memory.access(request()))==["中文"]
    assert memory.history(first["id"])[1]["retired_at"] is None  # Draft does not retire approved knowledge.
    rejected=memory.decide(draft["id"],ClaimDecision(expected_revision=draft["revision"],reason="拒绝此修订"),"reject")
    assert values(memory.access(request()))==["中文"]
    withdrawn=memory.decide(first["id"],ClaimDecision(expected_revision=rejected["revision"],reason="撤回"),"retract")
    assert memory.access(request())["path"]=="clarify"
    restored=memory.decide(first["id"],ClaimDecision(expected_revision=withdrawn["revision"],reason="明确恢复"),"restore")
    assert values(memory.access(request()))==["中文"] and restored["status"]=="approved"


def test_reinforcement_deduplicates_original_messages(core):
    first=approve(core,create(core))
    data={key:first[key] for key in ("value","polarity","modality","fact_start","fact_end","time_precision","memory_id","evidence")}
    reinforced=approve(core,core[1].revise(first["id"],ClaimRevision(expected_revision=first["revision"],action="reinforce",**data)))
    assert len(reinforced["support_group_ids"])==1 and len(reinforced["evidence"])==1
    assert len(core[1].list())==1


@pytest.mark.parametrize("change",["source_edit","session_delete","source_undo","parent_delete","parent_correct","parent_temporal"])
def test_external_lifecycle_invalidates_views_and_blocks_stale_state(core,change):
    store,memory,sid=core
    parent=store.add_memory("Engineering parent")
    first=approve(core,create(core,memory_id=parent["id"] if change.startswith("parent_") else None))
    view=memory.compile(ViewRequest(predicates=["response_language"]))
    mid=first["evidence"][0]["message_id"]
    if change=="source_edit":
        with store.db:store.db.execute("UPDATE messages SET content='changed' WHERE id=?",(mid,))
    if change=="session_delete":store.delete_session(sid)
    if change=="source_undo":
        store.undo_memory_sources([mid],dry_run=True)
        assert memory.view(view["id"])["valid"]
        store.undo_memory_sources([mid])
    if change=="parent_delete":store.delete_memory(parent["id"])
    if change=="parent_correct":store.correct_memory(parent["id"],"Changed parent","fact",3,"engineering correction")
    if change=="parent_temporal":store.set_temporal(parent["id"],invalid_at="2020-01-01T00:00:00Z",valid_at="2019-01-01T00:00:00Z")
    assert not memory.view(view["id"])["valid"]
    assert memory.access(request())["path"]=="clarify"
    assert store.db.execute("SELECT payload_json FROM claim_views WHERE id=?",(view["id"],)).fetchone()[0]=="{}"


def test_no_source_resurrection_via_restore_or_historical_cache(core):
    store,memory,sid=core
    first=approve(core,create(core))
    store.undo_memory_sources([first["evidence"][0]["message_id"]])
    withdrawn=memory.decide(first["id"],ClaimDecision(expected_revision=first["revision"],reason="来源撤回"),"retract")
    with pytest.raises(ClaimError):
        memory.decide(first["id"],ClaimDecision(expected_revision=withdrawn["revision"],reason="不能复活原话"),"restore")
    assert memory.access(request(recorded_as_of=first["recorded_at"]))["path"]=="clarify"
    assert memory.history(first["id"])[1]["value"] is None


def test_budget_keeps_groups_whole_and_empty_views_expire(core,monkeypatch):
    import memoria.claims as module
    approve(core,create(core))
    view=core[1].compile(ViewRequest(predicates=["response_language"],budget_chars=256,ttl_seconds=1))
    payload=view["payload"]
    assert not payload["complete"] and payload["claims"]==[] and len(canonical(payload))<=256
    assert core[1].access(request(budget_chars=256))["path"]=="clarify"
    monkeypatch.setattr(module,"now",lambda:view["expires_at"])
    assert not core[1].view(view["id"])["valid"]


def test_restore_can_use_prior_valid_version_without_restoring_revoked_source(core):
    first=approve(core,create(core))
    second=approve(core,revise(core,first,"English"))
    core[0].undo_memory_sources([second["evidence"][0]["message_id"]])
    restored=core[1].decide(second["id"],ClaimDecision(expected_revision=second["revision"],reason="恢复仍有有效原话的旧版本"),"restore")
    assert restored["value"]=="中文"
    assert restored["support_group_ids"]==first["support_group_ids"]
    assert values(core[1].access(request()))==["中文"]


@pytest.mark.parametrize("modality,predicate,complete",[("inference","residence",False),("plan","residence",False),("plan","learning_goal",True)])
def test_plans_and_inferences_do_not_become_current_completed_facts(core,modality,predicate,complete):
    approve(core,create(core,"Engineering value",predicate=predicate,modality=modality))
    result=core[1].access(request(predicate))
    assert result["payload"]["complete"] is complete
    if complete:assert result["payload"]["claims"][0]["modality"]=="plan"


def test_negative_constraint_does_not_answer_unknown_positive_value(core):
    approve(core,create(core,"A",predicate="residence",polarity="negative"))
    result=core[1].access(request("residence"))
    assert result["path"]=="clarify" and result["payload"]["missing"][0]["reason"]=="negative_constraints_only"
    assert result["payload"]["claims"][0]["polarity"]=="negative"


def test_extraction_creates_only_pending_source_located_candidates(core):
    from memoria.llm import ChatResult
    store,memory,sid=core
    source=store.add_message(sid,"user","Engineering original: 中文")
    class Model:
        async def chat(self,messages,**kwargs):
            assert kwargs["temperature"]==0 and messages[1]["content"].find(source["content"])>=0
            return ChatResult(content=json.dumps({"claims":[{"predicate":"response_language","value":"中文","evidence_quote":"中文"}]}),usage={"total_tokens":12})
    result=asyncio.run(memory.extract(Model(),ExtractionRequest(message_id=source["id"])))
    candidate=result["candidates"][0]
    assert candidate["status"]=="pending" and candidate["proposal_origin"]=="model" and candidate["reviewer"] is None
    assert candidate["fact_start"] is None and candidate["time_precision"]=="unknown"
    assert candidate["evidence"][0]["start"]==len(source["content"])-2
    assert result["automatic_approvals"]==0 and memory.access(request())["path"]=="clarify"


@pytest.mark.parametrize("failure",["ambiguous","invalid_second","source_changed","provider"])
def test_extraction_revalidates_source_and_invalid_batch_has_no_partial_writes(core,failure):
    from memoria.llm import ChatResult
    store,memory,sid=core
    source=store.add_message(sid,"user","Engineering repeated 中文 中文")
    class Model:
        async def chat(self,*args,**kwargs):
            if failure=="provider":raise RuntimeError("engineering model failure")
            if failure=="source_changed":
                with store.db:store.db.execute("UPDATE messages SET content='modified' WHERE id=?",(source["id"],))
            items=[{"predicate":"response_language","value":"中文","evidence_quote":"中文" if failure=="ambiguous" else source["content"]}]
            if failure=="invalid_second":items.append({"predicate":"unsupported","value":"x","evidence_quote":source["content"]})
            return ChatResult(content=json.dumps({"claims":items}))
    with pytest.raises(ClaimError):asyncio.run(memory.extract(Model(),ExtractionRequest(message_id=source["id"])))
    assert memory.list()==[]


def test_claim_deletion_invalidates_and_erases_revision_records(core):
    first=approve(core,create(core))
    view=core[1].compile(ViewRequest(predicates=["response_language"]))
    core[1].delete(first["id"])
    assert not core[1].view(view["id"])["valid"]
    assert core[0].db.execute("SELECT count(*) FROM claim_revisions").fetchone()[0]==0
    with pytest.raises(ClaimError):core[1].get(first["id"])


def test_immutable_revisions_and_owner_binding(core):
    first=approve(core,create(core))
    with pytest.raises(sqlite3.IntegrityError):
        with core[0].db:core[0].db.execute("UPDATE claim_revisions SET status='pending' WHERE claim_id=?",(first["id"],))
    with pytest.raises(ClaimError):ClaimMemory(core[0],"different-account")


def test_schema_setup_never_commits_a_callers_unrelated_transaction(core):
    store=core[0]
    store.db.execute("BEGIN")
    try:
        store.db.execute("UPDATE sessions SET title='uncommitted'")
        with pytest.raises(RuntimeError):ClaimMemory(store,"engineering-owner")
        assert store.db.in_transaction
    finally:store.db.rollback()
    assert store.session(core[2])["title"]=="Temporary engineering case"


def test_cache_storage_is_bounded_and_invalidated_payload_is_not_reused(core):
    approve(core,create(core))
    first=core[1].compile(ViewRequest(predicates=["response_language"]))
    for i in range(130):core[1].compile(ViewRequest(predicates=["response_language"],context={"request_number":i}))
    assert core[0].db.execute("SELECT count(*) FROM claim_views").fetchone()[0]==128
    with pytest.raises(ClaimError):core[1].view(first["id"])


def test_atomic_transition_rollback_preserves_old_claim_and_cache(core,monkeypatch):
    memory=core[1]
    first=approve(core,create(core,"A",predicate="residence",fact_start="2023-01-01T00:00:00Z",time_precision="day"))
    successor=revise(core,first,"B",action="transition",fact_start="2024-01-01T00:00:00Z")
    view=memory.compile(ViewRequest(predicates=["residence"]))
    original=memory._append
    def fail(root,data,status,reason,**kwargs):
        if root["id"]==successor["id"] and status=="approved":raise RuntimeError("engineering injected failure")
        return original(root,data,status,reason,**kwargs)
    monkeypatch.setattr(memory,"_append",fail)
    with pytest.raises(RuntimeError):approve(core,successor)
    assert memory.get(first["id"])["revision"]==first["revision"]
    assert memory.view(view["id"])["valid"] and values(memory.access(request("residence")))==["A"]


def test_two_connections_cannot_double_approve(core,tmp_path):
    candidate=create(core)
    second=Store(tmp_path/"claims.db",vector_backend="json")
    other=ClaimMemory(second,"engineering-owner")
    barrier=Barrier(2)
    def publish(memory):
        barrier.wait()
        try:
            memory.approve(candidate["id"],ClaimDecision(expected_revision=1,reason="concurrent engineering approval"))
            return "approved"
        except ClaimError as error:
            return error.status
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(publish,[core[1],other]),key=str)==[409,"approved"]
        assert len(core[1].history(candidate["id"]))==2
    finally:second.close()


def test_tool_and_narrow_natural_language_access(core):
    approve(core,create(core))
    registry=build_registry(core[0],claims=core[1])
    result=asyncio.run(registry.execute("access_claims",{"predicates":["response_language"]}))
    assert result.ok and values(json.loads(result.content))==["中文"]
    result=asyncio.run(registry.execute("access_claims",{"predicates":["response_language"],"user_id":"other"}))
    assert not result.ok
    assert infer_access("我现在住在哪里？").predicates==["residence"]
    assert infer_access("我的回答语言偏好？").predicates==["response_language"]
    assert infer_access("帮我写代码，他住在哪里？") is None
