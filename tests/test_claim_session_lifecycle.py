"""Temporary session conditions tested in isolation, never research data."""
import asyncio
import json

import pytest

from memoria.claims import (AccessRequest, ClaimDecision, ClaimDraft, ClaimError,
                            ClaimMemory, ClaimRevision, ViewRequest, sha)
from memoria.memory import MemoryEngine
from memoria.memory.access import MemoryAccessOrchestrator, QueryRequest
from memoria.memory.claim_relations import ClaimRelationResolver
from memoria.memory.reader import CitationGuard
from memoria.store import Store


@pytest.fixture
def core(tmp_path):
    store = Store(tmp_path / "sessions.db", "json")
    claims = ClaimMemory(store, "engineering-owner")
    sessions = [store.create_session("Temporary engineering session")["id"] for _ in range(2)]
    yield store, claims, sessions
    store.close()


def evidence(core, session, text):
    message = core[0].add_message(session, "user", text)
    return dict(message_id=message["id"], start=0, end=len(text), quote=text, content_sha256=sha(text))


def create(core, value, session, scope):
    source = evidence(core, session, f"Engineering preference {value}, for this session.")
    return core[1].create(ClaimDraft(predicate="response_language", value=value, evidence=[source], scope=scope))


def approve(core, candidate):
    return core[1].approve(candidate["id"], ClaimDecision(expected_revision=candidate["revision"], reason="Engineering explicit approval"))


def request(session=None, **kwargs):
    return AccessRequest(predicates=["response_language"], context={"activity":"interview"}, session_id=session, **kwargs)


def values(result):
    return [c["value"] for c in result["payload"]["claims"]]


def test_local_exception_overrides_only_bound_session_and_cache_keys_are_distinct(core):
    sid, other = core[2]
    approve(core, create(core, "中文", sid, {}))
    approve(core, create(core, "English", sid, {"activity":"interview"}))
    local = approve(core, create(core, "简体中文", sid, {"activity":"interview","temporary":True}))
    assert local["lifecycle"] == {"kind":"session", "session_id":sid, "origin":"source_session"}
    assert values(core[1].access(request(sid))) == ["简体中文"]
    assert values(core[1].access(request(other))) == ["English"]
    assert core[1].access(request())["path"] == "clarify"
    view = core[1].compile(ViewRequest(**request(sid).model_dump()))
    assert core[1].view(view["id"])["valid"]
    assert core[1].access(request(other))["view_id"] != view["id"]
    assert values(core[1].access(AccessRequest(predicates=["response_language"],scope_mode="default"))) == ["中文"]


def test_two_sessions_can_approve_different_local_values_without_conflicting(core):
    left = approve(core, create(core, "English", core[2][0], {"activity":"interview","temporary":True}))
    right = approve(core, create(core, "中文", core[2][1], {"activity":"interview","temporary":True}))
    assert not core[1]._overlap(left, right)
    assert ClaimRelationResolver().pair(core[1], left, right, {}) == "disjoint_scope"
    assert values(core[1].access(request(core[2][0]))) == ["English"]
    assert values(core[1].access(request(core[2][1]))) == ["中文"]


def test_cross_session_premises_and_reinforcement_cannot_rebind_temporary_scope(core):
    sid, other = core[2]
    source = evidence(core, sid, "Engineering preference English for this session")
    condition = evidence(core, other, "Engineering current interview")
    with pytest.raises(ClaimError, match="同一来源会话"):
        core[1].create(ClaimDraft(predicate="response_language",value="English",evidence=[source],
            condition_evidence=[condition],scope={"activity":"interview","temporary":True}))
    current = approve(core, create(core, "English", sid, {"activity":"interview","temporary":True}))
    fields = {k:current[k] for k in ("value","polarity","modality","fact_start","fact_end","time_precision","memory_id")}
    with pytest.raises(ClaimError):
        core[1].revise(current["id"], ClaimRevision(**fields,expected_revision=current["revision"],action="reinforce",
            evidence=[evidence(core, other, "Engineering renewed English")],condition_evidence=[condition]))
    assert core[1].get(current["id"])["revision"] == current["revision"]


def test_source_session_move_invalidates_pending_approval_and_approved_views(core):
    sid, other = core[2]
    candidate = create(core, "English", sid, {"temporary":True})
    mid = candidate["evidence"][0]["message_id"]
    with core[0].db:
        core[0].db.execute("UPDATE messages SET session_id=? WHERE id=?", (other,mid))
    with pytest.raises(ClaimError):
        approve(core, candidate)
    current = approve(core, create(core, "中文", sid, {"temporary":True}))
    view = core[1].compile(ViewRequest(**request(sid).model_dump()))
    with core[0].db:
        core[0].db.execute("UPDATE messages SET session_id=? WHERE id=?", (other,current["evidence"][0]["message_id"]))
    assert not core[1].view(view["id"])["valid"]
    assert not values(core[1].access(request(sid)))


def test_unknown_legacy_temporary_binding_stays_unknown(core):
    sid = core[2][0]
    current = approve(core, create(core, "English", sid, {"temporary":True}))
    data = json.loads(core[1]._head(current["id"])["data_json"])
    data.pop("lifecycle")
    with core[0]._memory_transaction():
        core[1]._append(core[1]._root(current["id"]), data, "approved", "Legacy fixture")
    assert core[1].access(request(sid))["path"] == "clarify"


def test_query_and_reader_carry_session_and_fail_closed_if_it_disappears(core):
    sid = core[2][0]
    approve(core, create(core, "English", sid, {"activity":"interview","temporary":True}))
    query = QueryRequest(query="面试模拟时我用什么语言回答",session_id=sid)
    result = asyncio.run(MemoryAccessOrchestrator(core[1],MemoryEngine(core[0])).access(query))
    assert result["path"] in {"view","retrieve"}
    guard = CitationGuard(core[1],result,query)
    assert guard.freshness() is None
    core[0].delete_session(sid)
    assert guard.freshness() == "query_session_unavailable"


def test_unknown_or_other_account_session_is_not_a_context_override(core):
    with pytest.raises(ClaimError, match="当前账号"):
        core[1].access(request("missing-session"))
    with pytest.raises(ClaimError, match="当前账号"):
        asyncio.run(MemoryAccessOrchestrator(core[1],MemoryEngine(core[0])).access(QueryRequest(query="hello",session_id="missing-session")))


@pytest.mark.parametrize("text", ["我暂时在面试模拟时用英文。", "我下周这次面试时希望用英文。"])
def test_temporary_or_event_wording_does_not_invent_a_session_expiry(core, text):
    candidate = core[1].create(ClaimDraft(predicate="response_language",value="English",scope={"activity":"interview","temporary":True},
        evidence=[evidence(core, core[2][0], text)]))
    assert candidate.get("lifecycle") is None and candidate["status"] == "pending"
    with pytest.raises(ClaimError, match="会话绑定"):
        approve(core, candidate)
