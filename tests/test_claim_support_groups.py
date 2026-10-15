"""Isolated source-lifecycle regressions; these are not research histories/gold."""
import json

import pytest

from memoria.claims import (AccessRequest, ClaimDecision, ClaimDraft, ClaimError,
                            ClaimMemory, ClaimRevision, ViewRequest, sha)
from memoria.store import Store


@pytest.fixture
def core(tmp_path):
    store = Store(tmp_path / "groups.db", "json")
    claims = ClaimMemory(store, "engineering-owner")
    sid = store.create_session("Support group regression")["id"]
    yield store, claims, sid
    store.close()


def source(core, text):
    message = core[0].add_message(core[2], "user", text)
    return dict(message_id=message["id"], start=0, end=len(text), quote=text, content_sha256=sha(text))


def approve(core, draft):
    return core[1].approve(draft["id"], ClaimDecision(expected_revision=draft["revision"], reason="Engineering approval"))


def initial(core, **kwargs):
    return approve(core, core[1].create(ClaimDraft(predicate="response_language", value="English",
        evidence=[source(core, "Engineering original preference: English")], **kwargs)))


def reinforce(core, current, **kwargs):
    fields = {key:current[key] for key in ("value", "polarity", "modality", "fact_start", "fact_end", "time_precision", "memory_id")}
    fields["evidence"] = kwargs.pop("evidence", None) or [source(core, "Engineering renewed preference: English")]
    fields.update(kwargs)
    return core[1].revise(current["id"], ClaimRevision(expected_revision=current["revision"], action="reinforce", **fields))


@pytest.mark.parametrize("mutation", ["revoke", "delete", "change", "role"])
def test_other_complete_approved_group_survives_and_views_drop_dead_spans(core, mutation):
    first = initial(core)
    current = approve(core, reinforce(core, first))
    request = AccessRequest(predicates=["response_language"])
    before = core[1].access(request)
    dependency = core[1].access_dependency(request, before["payload"])
    cached = core[1].compile(ViewRequest(predicates=request.predicates))
    mid = first["evidence"][0]["message_id"]
    if mutation == "revoke":
        core[0].undo_memory_sources([mid])
    else:
        with core[0].db:
            if mutation == "delete":
                core[0].db.execute("DELETE FROM messages WHERE id=?", (mid,))
            elif mutation == "change":
                core[0].db.execute("UPDATE messages SET content='Changed' WHERE id=?", (mid,))
            else:
                core[0].db.execute("UPDATE messages SET role='assistant' WHERE id=?", (mid,))
    assert not core[1].view(cached["id"])["valid"]
    assert not core[1].access_dependency_valid(dependency)
    result = core[1].access(request)
    assert result["payload"]["complete"]
    visible = result["payload"]["claims"][0]
    assert visible["value"] == "English" and len(visible["support_groups"]) == 1
    assert mid not in json.dumps(visible)
    assert len(json.loads(core[1]._head(current["id"])["data_json"])["support_groups"]) == 2
    core[0].undo_memory_sources(visible["support_group_ids"])
    assert core[1].access(request)["path"] == "clarify"


def test_within_group_is_and_and_new_condition_support_is_required(core):
    condition = source(core, "Engineering scope: interview")
    first = initial(core, scope={"activity":"interview"}, condition_evidence=[condition])
    with pytest.raises(ClaimError, match="条件证据"):
        reinforce(core, first)
    other_condition = source(core, "Engineering renewed scope: interview")
    current = approve(core, reinforce(core, first, condition_evidence=[other_condition]))
    core[0].undo_memory_sources([condition["message_id"], current["evidence"][-1]["message_id"]])
    # One surviving value in A and one surviving condition in B cannot be spliced.
    request = AccessRequest(predicates=["response_language"], context={"activity":"interview"})
    assert core[1].access(request)["path"] == "clarify"


def test_unapproved_reinforcement_cannot_replace_withdrawn_support(core):
    first = initial(core)
    draft = reinforce(core, first)
    core[0].undo_memory_sources(first["support_group_ids"])
    assert core[1].access(AccessRequest(predicates=["response_language"]))["path"] == "clarify"
    approved = approve(core, draft)
    assert approved["source_available"] and len(approved["support_groups"]) == 1


def test_pending_new_support_revoked_before_approval_is_rejected(core):
    first = initial(core)
    draft = reinforce(core, first)
    new_ids = set(draft["support_group_ids"]) - set(first["support_group_ids"])
    core[0].undo_memory_sources(list(new_ids))
    with pytest.raises(ClaimError):
        approve(core, draft)
    assert core[1].access(AccessRequest(predicates=["response_language"]))["payload"]["complete"]


def test_exact_copies_are_one_lineage_and_fact_retraction_blocks_all_groups(core):
    first = initial(core)
    copy = source(core, first["evidence"][0]["quote"])
    current = approve(core, reinforce(core, first, evidence=[copy]))
    assert len(current["support_groups"]) == 1
    core[0].undo_memory_sources(first["support_group_ids"])
    assert not core[1].get(current["id"])["source_available"]
    # Another independently approved support does not bypass a fact-level decision.
    draft = reinforce(core, current)
    current = approve(core, draft)
    core[1].decide(current["id"], ClaimDecision(expected_revision=current["revision"], reason="Engineering retract"), "retract")
    assert core[1].access(AccessRequest(predicates=["response_language"]))["path"] == "clarify"


def test_legacy_flat_list_remains_one_conjunction(core):
    first = initial(core)
    second = source(core, "Engineering second required premise")
    row = core[1]._head(first["id"])
    data = json.loads(row["data_json"])
    data.pop("support_groups")
    data["evidence"].append({k:v for k,v in second.items() if k != "quote"})
    with core[0]._memory_transaction():
        core[1]._append(core[1]._root(first["id"]), data, "approved", "Legacy-format fixture")
    assert len(core[1].get(first["id"])["support_groups"]) == 1
    core[0].undo_memory_sources([second["message_id"]])
    assert not core[1].get(first["id"])["source_available"]


def test_recorded_cutoff_does_not_see_later_support(core):
    first = initial(core)
    approve(core, reinforce(core, first))
    core[0].undo_memory_sources(first["support_group_ids"])
    result = core[1].access(AccessRequest(predicates=["response_language"], recorded_as_of=first["recorded_at"]))
    assert result["path"] == "clarify"
