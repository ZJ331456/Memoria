"""Isolated source support regressions; no real personal histories or semantic gold."""
import asyncio
import json

import pytest

from memoria.claims import ClaimMemory, ClaimDraft, ClaimDecision, ViewRequest, sha
from memoria.memory import MemoryEngine
from memoria.memory.access import MemoryAccessOrchestrator, QueryRequest
from memoria.memory.reader import CitationGuard
from memoria.memory.source_support import live_groups
from memoria.store import Store


@pytest.fixture(params=['json','sqlite-vec'])
def core(tmp_path,request):
    store=Store(tmp_path/'ordinary.db',request.param)
    if request.param=='sqlite-vec' and store.vector_index_status['backend']!='sqlite-vec':
        store.close();pytest.skip('sqlite-vec unavailable')
    sid=store.create_session('Engineering source groups')['id']
    yield store,sid
    store.close()


def source(core,text):return core[0].add_message(core[1],'user',text)


def memory(core):
    first=source(core,'Engineering initial confirmation: concise answers are preferred.')
    item=core[0].add_memory('concise answers','preference',source_ref=first['id'],require_source=True)
    second=source(core,'Engineering independent later wording confirms concise answers.')
    action,item=core[0].add_memory_if_absent('concise answers','preference',3,'reviewed_conversation',source_ref=second['id'],require_source=True)
    assert action=='reinforced' and len(live_groups(core[0],item['id']))==2
    return first,second,item


@pytest.mark.parametrize('mutation',['revoke','delete','change','session'])
def test_complete_alternative_survives_first_source_loss_and_last_loss_hides_history(core,mutation):
    store,sid=core;first,second,item=memory(core)
    at=item['created_at']
    if mutation=='revoke':store.undo_memory_sources([first['id']])
    else:
        with store._memory_transaction():
            if mutation=='delete':store.db.execute('DELETE FROM messages WHERE id=?',(first['id'],))
            elif mutation=='change':store.db.execute("UPDATE messages SET content='changed source' WHERE id=?",(first['id'],))
            else:
                other=store.create_session('Other engineering session')['id']
                store.db.execute('UPDATE messages SET session_id=? WHERE id=?',(other,first['id']))
    assert store.memory_support_available(item['id'])
    assert item['id'] in {m['id'] for m in store.retrieval_memories()}
    assert item['id'] in {m['id'] for m in store.retrieval_memories(as_of=at)}
    assert store.memory_source(item['id'])['message_id']==second['id']
    store.undo_memory_sources([second['id']])
    assert not store.memory_support_available(item['id'])
    assert item['id'] not in {m['id'] for m in store.retrieval_memories(as_of=at)}


def test_last_support_withdrawal_restores_previous_version_once(core):
    store,_=core
    oldsrc=source(core,'Engineering initial detailed preference.')
    old=store.add_memory('detailed answers','preference',source_ref=oldsrc['id'],require_source=True)
    a=source(core,'Engineering changed preference to concise answers.')
    new=store.add_memory('concise answers','preference',source_ref=a['id'],require_source=True,supersedes_id=old['id'])
    b=source(core,'Engineering another wording confirms concise preference.')
    store.add_memory_if_absent('concise answers','preference',3,'reviewed_conversation',source_ref=b['id'],require_source=True)
    preview=store.undo_memory_sources([a['id']],dry_run=True)
    assert not preview['restored_ids'] and not store.source_revoked(a['id'])
    store.undo_memory_sources([a['id']])
    assert store.memory(new['id'])['status']=='active' and store.memory(old['id'])['status']=='superseded'
    result=store.undo_memory_sources([b['id']])
    assert result['restored_ids']==[old['id']]
    assert store.memory(old['id'])['status']=='active' and store.memory(new['id'])['status']=='superseded'
    assert store.undo_memory_sources([b['id']])['restored_ids']==[]


def test_similarity_operation_alone_is_not_a_substitute_and_exact_copy_is_not_new_lineage(core):
    store,_=core
    a=source(core,'Engineering repeated original.')
    m=store.add_memory('concise answers','preference',source_ref=a['id'],require_source=True)
    b=source(core,'Engineering different detail, not explicit full support.')
    store.reinforce_memory(m['id'],b['id'],require_source=True)
    copied=source(core,a['content'])
    store.add_memory_if_absent('concise answers','preference',3,'reviewed_conversation',source_ref=copied['id'],require_source=True)
    assert len(live_groups(store,m['id']))==1
    store.undo_memory_sources([a['id']])
    assert not store.retrieval_memories()


def test_support_conjunction_and_body_version_are_not_merged_into_any_fragment(core):
    store,_=core
    a=source(core,'Engineering primary value.')
    b=source(core,'Engineering required condition.')
    m=store.add_memory('conditioned memory','fact',source_ref=a['id'],require_source=True)
    group=live_groups(store,m['id'])[0]
    with store._memory_transaction():
        store.db.execute('INSERT INTO memory_support_evidence VALUES(?,?,?,?,?,?)',
            (group['id'],b['id'],sha(b['content']),b['session_id'],b['created_at'],'active'))
    assert store.memory_support_available(m['id'])
    with store._memory_transaction():store.db.execute('DELETE FROM messages WHERE id=?',(b['id'],))
    assert not store.memory_support_available(m['id'])
    with store._memory_transaction():store.db.execute("UPDATE memories SET content='different body' WHERE id=?",(m['id'],))
    assert not store.memory_support_available(m['id'])


def test_unified_retrieval_cites_surviving_source_and_reader_rechecks_it(core):
    store,_=core;first,second,item=memory(core)
    claims=ClaimMemory(store,'engineering-owner')
    store.undo_memory_sources([first['id']])
    req=QueryRequest(query='concise answers',mode='bm25',include_experiences=False)
    packet=asyncio.run(MemoryAccessOrchestrator(claims,MemoryEngine(store)).access(req))
    assert packet['candidates'] and packet['candidates'][0]['source_ids']==[second['id']]
    guard=CitationGuard(claims,packet,req)
    assert guard.freshness() is None
    store.undo_memory_sources([second['id']])
    assert guard.freshness() is not None


def test_linked_claim_cannot_bypass_parent_support_loss(core):
    store,_=core
    a=source(core,'工程原话：我住在南京。')
    m=store.add_memory('我住在南京','profile',source_ref=a['id'],require_source=True)
    b=source(core,'工程再次确认：我住在南京。')
    store.add_memory_if_absent('我住在南京','profile',3,'reviewed_conversation',source_ref=b['id'],require_source=True)
    c=source(core,'工程独立原话：我住在南京。')
    claims=ClaimMemory(store,'engineering-owner')
    draft=claims.create(ClaimDraft(predicate='residence',value='南京',memory_id=m['id'],
        evidence=[dict(message_id=c['id'],start=0,end=len(c['content']),quote=c['content'],content_sha256=sha(c['content']))]))
    claims.approve(draft['id'],ClaimDecision(expected_revision=draft['revision'],reason='Engineering approval'))
    store.undo_memory_sources([a['id']])
    assert claims.compile(ViewRequest(predicates=['residence']))['payload']['complete']
    with store._memory_transaction():store.db.execute("UPDATE messages SET content='changed' WHERE id=?",(b['id'],))
    assert not claims.compile(ViewRequest(predicates=['residence']))['payload']['complete']


def test_failed_support_write_rolls_back_operation_and_counter(core):
    store,_=core;first,second,item=memory(core)
    third=source(core,'Engineering third different confirmation.')
    before=store.memory(item['id'])['reinforcement']
    store.db.execute("""CREATE TEMP TRIGGER fail_support BEFORE INSERT ON memory_support_evidence
        BEGIN SELECT RAISE(ABORT,'injected support failure'); END""")
    with pytest.raises(Exception,match='injected support failure'):
        store.add_memory_if_absent('concise answers','preference',3,'reviewed_conversation',source_ref=third['id'],require_source=True)
    assert not store.has_memory_operation(third['id'],item['id'])
    assert store.memory(item['id'])['reinforcement']==before and len(live_groups(store,item['id']))==2


def test_legacy_reinforcement_is_not_migrated_as_new_independent_support(core,tmp_path):
    store,_=core
    a=source(core,'Engineering legacy original.')
    m=store.add_memory('legacy preference','preference',source_ref=a['id'],require_source=True)
    b=source(core,'Engineering legacy operation with no captured evidence.')
    store.reinforce_memory(m['id'],b['id'],require_source=True)
    with store._memory_transaction():store.db.execute('DELETE FROM memory_support_groups WHERE memory_id=?',(m['id'],))
    other=Store(tmp_path/'ordinary.db','json')
    try:
        groups=live_groups(other,m['id'])
        assert len(groups)==1 and groups[0]['origin']=='legacy_primary'
        assert groups[0]['evidence'][0]['message_id']==a['id']
        other.undo_memory_sources([a['id']])
        assert not other.memory_support_available(m['id'])
    finally:other.close()


def test_source_confirmation_does_not_erase_original_untracked_manual_note(core):
    store,_=core
    m=store.add_memory('manual note','fact')
    src=source(core,'Engineering source confirms this manual note.')
    store.add_memory_if_absent('manual note','fact',3,'reviewed_conversation',source_ref=src['id'],require_source=True)
    store.undo_memory_sources([src['id']])
    assert store.memory_support_available(m['id']) and store.memory_source(m['id']) is None
    assert m['id'] in {r['id'] for r in store.retrieval_memories()}
