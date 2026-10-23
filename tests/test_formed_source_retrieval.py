"""Synthetic reviewed-memory routing and complete Reader-packet contracts."""
import asyncio
from copy import deepcopy
import json

import pytest

from memoria.memory.adaptive_retrieval import Sources, merge_candidates
from memoria.memory.evidence_sufficiency import Quote, validate_quotes
from memoria.memory.library import RevisionInput, SourceInput
from memoria.memory.records import KnowledgeAnswer, MemorySearch
from memoria.memory.source_formation import OBSERVE, CONSOLIDATE
from memoria.llm.types import ChatResult
from tests.test_source_formation import env, ready, form, model
from tests.test_library_hybrid import Encoder
from tests.test_source_units import transcript, observation, model as unit_model

__all__ = ['env']


def reviewed_turn(env, *, summary=None, scope='personal', session_id=None):
    service, library, formation = env
    body = 'I brought home a kitten yesterday.'
    text, units = transcript(('Alice', body))
    source = library.create(SourceInput(kind='note', authorship='external', title='Synthetic transcript',
        content=text, source_units=units, scope=scope, session_id=session_id))
    proposal = observation(quote=body)
    proposal['content'] = summary or 'Alice reported pet adoption yesterday.'
    unit_model(service, proposal)
    receipt = form(formation, source)
    assert receipt['observations'][0]['status'] == 'approved'
    return source, body, receipt['observations'][0]


def retrieve(service, *, enabled=True, query='pet adoption'):
    adapter = Sources(service.kernel, KnowledgeAnswer(query=query, memory_mode='bm25',
        include_formed_memory=enabled, use_adaptive_retrieval=True))
    return adapter, asyncio.run(adapter.retrieve())


def test_reviewed_description_recalls_original_turn_and_summary_is_not_quote_evidence(env):
    service, library, _ = env
    source, body, observation = reviewed_turn(env)
    _, disabled = retrieve(service, enabled=False)
    assert disabled['candidates'] == []
    adapter, access = retrieve(service)
    candidate = access['candidates'][0]
    assert candidate['data']['source_id'] == source['id']
    assert candidate['data']['content'] == body
    organization = candidate['data']['provenance']['formed_memory']
    assert organization['role'] == 'retrieval_organization_only'
    assert organization['observations'][0]['observation_id'] == observation['id']
    assert organization['observations'][0]['summary'] == 'Alice reported pet adoption yesterday.'
    assert library.dependency_valid(candidate['dependency'])
    guard, messages, evidence, _ = adapter.present(access, [candidate])
    assert guard.freshness() is None
    assert organization['observations'][0]['summary'] in json.dumps(messages)
    validate_quotes([Quote(id=candidate['id'], quote=body)], evidence)
    with pytest.raises(ValueError, match='exact source text'):
        validate_quotes([Quote(id=candidate['id'], quote=organization['observations'][0]['summary'])], evidence)
    report = access['retrieval']['library']
    assert report['matched_observations'] == report['observation_hint_passages'] == 1
    assert report['formed_memory_enabled']


@pytest.mark.parametrize('change', ['retract', 'revise', 'unapprove'])
def test_formed_route_and_summary_dependency_expire_with_the_review_or_source(env, change):
    service, library, _ = env
    source, body, observation = reviewed_turn(env)
    _, access = retrieve(service)
    dep = access['candidates'][0]['dependency']
    if change == 'retract':
        library.retract(source['id'])
    elif change == 'revise':
        library.revise(source['id'], RevisionInput(expected_revision_id=source['head_id'],
            title='Revised synthetic source', content='Unrelated source.'))
    else:
        with service.store.memory_transaction():
            service.store.db.execute("UPDATE library_cards SET status='needs_assistance' WHERE id=?",
                                     (observation['card_id'],))
    assert not library.dependency_valid(dep)
    assert retrieve(service)[1]['candidates'] == []
    if change == 'unapprove':
        # Review revocation removes the summary; the independently live raw
        # document remains retrievable using its actual words.
        raw = retrieve(service, query='kitten')[1]['candidates'][0]
        assert raw['data']['content'] == body
        assert 'formed_memory' not in raw['data']['provenance']


def test_formed_descriptions_respect_working_session_before_the_bounded_scan(env):
    service, _, _ = env
    own = service.store.create_session('Synthetic own')['id']
    other = service.store.create_session('Synthetic other')['id']
    source, _, _ = reviewed_turn(env, scope='working', session_id=own)
    request = MemorySearch(query='pet adoption', domains=['library'], memory_mode='bm25')
    assert not asyncio.run(service.kernel.search(request))['evidence']
    assert not asyncio.run(service.kernel.search(request.model_copy(update={'session_id': other})))['evidence']
    result = asyncio.run(service.kernel.search(request.model_copy(update={'session_id': own})))
    assert result['evidence'][0]['source_id'] == source['id']
    assert result['retrieval']['library']['live_observations'] == 1


def test_query_independent_organization_survives_targeted_supplement_merge(env):
    service, _, _ = env
    reviewed_turn(env)
    adapter, first = retrieve(service)
    second = asyncio.run(adapter.retrieve('Alice adoption'))
    combined = merge_candidates(first['candidates'], second['candidates'])
    assert len(combined) == 1
    assert combined[0]['data']['provenance']['formed_memory'] == second['candidates'][0]['data']['provenance']['formed_memory']


def test_oversized_summary_keeps_raw_reader_evidence_without_partial_conditions(env):
    service, _, _ = env
    summary = 'Alice reported pet adoption, based only on this attributed source. ' * 17
    _, body, _ = reviewed_turn(env, summary=summary)
    adapter, access = retrieve(service)
    candidate = access['candidates'][0]
    organization = candidate['data']['provenance']['formed_memory']
    assert organization['observations'][0]['summary_omitted'] == 'organization_budget'
    assert 'summary' not in organization['observations'][0]
    assert candidate['data']['content'] == body
    assert len(json.dumps(adapter.render(access, [candidate]), ensure_ascii=False)) < 8000
    assert adapter.present(access, [candidate])[0].freshness() is None


def test_source_only_disables_consolidations_and_all_summary_organization(env):
    service, _, _ = env
    ready(env)
    request = MemorySearch(query='Memoria部署', domains=['library'], memory_mode='bm25', include_formed_memory=False)
    capture = {}
    result = asyncio.run(service.kernel.search(request, capture=capture))
    assert result['evidence']
    assert all(e['representation'] == 'source_quote' for e in capture['candidates'])
    assert all(not e['provenance']['knowledge_cards'] and not e['provenance']['observations']
               and 'formed_memory' not in e['provenance']
               for e in capture['candidates'])
    assert not capture['retrieval']['library']['formed_memory_enabled']
    assert not any(dep.get('formation_refs') for dep in capture['candidate_dependencies'])


def test_complete_consolidation_packet_deduplicates_storage_without_losing_support(env):
    service, library, formation = env
    _, _, head = ready(env)
    materialized = formation.read(head['root_id'])
    record = service.kernel.readers.library_hit(materialized)
    assert len(record.provenance['support']) == len(materialized['support']) == 3
    for compacted, original in zip(record.provenance['support'], materialized['support'], strict=True):
        assert compacted['quote'] == original['quote']
        assert compacted['citation'] == original['citation']
        assert compacted['applicability'] == original['applicability']
        assert compacted['feedback_quote'] == original['feedback_quote']
        if compacted.get('subject_evidence_in_quote'):
            assert original['subject_quote'] in compacted['quote']
    assert len(json.dumps(record.public(), ensure_ascii=False)) < len(json.dumps(materialized, ensure_ascii=False))
    assert formation.dependency_valid(record.dependency)


def test_organization_fingerprint_and_malformed_refs_cannot_bypass_dependencies(env):
    service, library, _ = env
    reviewed_turn(env)
    _, access = retrieve(service)
    dep = access['candidates'][0]['dependency']
    for key, value in [('sha256', 'wrong'), ('card_id', 'missing'), ('observation_id', [])]:
        bad = deepcopy(dep)
        bad['formation_refs'][0][key] = value
        assert not library.dependency_valid(bad)


def test_summary_dependency_hash_preserves_field_boundaries(env):
    service, library, _ = env
    _, _, observation = reviewed_turn(env)
    _, access = retrieve(service)
    dep = access['candidates'][0]['dependency']
    card = service.store.db.execute('SELECT * FROM library_cards WHERE id=?', (observation['card_id'],)).fetchone()
    # The concatenated bytes stay identical, but the meaning of the two fields
    # changes. A structural snapshot must still expire.
    content = card['content'] + card['applicability'][0]
    applicability = card['applicability'][1:]
    assert content + applicability == card['content'] + card['applicability']
    with service.store.memory_transaction():
        service.store.db.execute('UPDATE library_cards SET content=?,applicability=? WHERE id=?',
                                 (content, applicability, observation['card_id']))
    assert not library.dependency_valid(dep)


def test_review_revoked_during_embedding_cannot_steer_or_annotate_the_reader(env):
    service, _, _ = env
    _, _, observation = reviewed_turn(env)
    encoder = Encoder()
    service.kernel.memory.embedder = encoder
    def unapprove():
        with service.store.memory_transaction():
            service.store.db.execute("UPDATE library_cards SET status='needs_assistance' WHERE id=?",
                                     (observation['card_id'],))
    encoder.after_query = unapprove
    request = MemorySearch(query='pet adoption', domains=['library'], memory_mode='rrf')
    result = asyncio.run(service.kernel.search(request))
    assert result['evidence'] == []
    assert result['retrieval']['library']['matched_observations'] == 0
    assert result['retrieval']['library']['observation_hint_passages'] == 0


def test_compact_consolidation_keeps_raw_subject_and_source_unit_date_metadata(env):
    service, library, formation = env
    def alter(stage, payload, output, _):
        if stage == OBSERVE:
            output['observations'][0].update(topic='pet adoption', subject='Alice', source_unit_id='t0',
                content='Alice reported pet adoption.', quote='I brought home a kitten yesterday.')
        elif stage == CONSOLIDATE:
            output.update(content='Alice reported pet adoption.', applicability='Only attributed reports.')
    model(service, alter=alter)
    for i in range(3):
        text, units = transcript(('Alice', f'I brought home a kitten yesterday. Reference {i}.'))
        source = library.create(SourceInput(kind='note', authorship='external', title='Synthetic transcript',
            content=text, source_units=units))
        receipt = form(formation, source)
    assert receipt['consolidation']['status'] == 'approved'
    materialized = formation.read(receipt['consolidation']['root_id'])
    record = service.kernel.readers.library_hit(materialized)
    assert len(record.provenance['support']) == 3
    for entry, original in zip(record.provenance['support'], materialized['support'], strict=True):
        assert entry['source_unit']['id'] == 't0'
        assert entry['source_unit']['speaker'] == 'Alice'
        assert entry['source_unit']['session_date_raw'] == '1:56 pm on 8 May, 2023'
        assert entry['subject_quote'] == original['subject_quote']  # header lies outside body quote
        assert entry['subject_citation'] == original['subject_citation']
        revision = library._revision(original['revision_id'])
        assert revision['content'][original['start']:original['end']] == entry['quote']
    assert formation.dependency_valid(record.dependency)


def test_nonadaptive_kernel_answer_keeps_consolidation_lineage_and_trace_contract(env):
    from memoria.prompting.assembler import _parse_frame
    service, _, _ = env
    _, sources, head = ready(env)
    calls = []
    async def reader(messages, **_):
        _, sections = _parse_frame(messages[1]['content'])
        packet = json.loads(next(s.content for s in sections if s.name == 'Source Evidence').rsplit('\n', 1)[-1])
        memory = next(e for e in packet['evidence'] if e['representation'] == 'consolidated_memory')
        assert len(memory['provenance']['support']) == 3
        for support in memory['provenance']['support']:
            assert support['source_id'] in {s['id'] for s in sources}
            assert isinstance(support['revision_id'], str)
            assert 0 <= support['start'] < support['end']
        calls.append(memory['citation'])
        return ChatResult(content=json.dumps({'status': 'answer',
            'answer': 'Memoria部署流程包含配置核对。', 'evidence_ids': [memory['citation']]}, ensure_ascii=False),
            raw_message={}, usage={'total_tokens': 20}, finish_reason='stop')
    service.llm.chat = reader
    result = asyncio.run(service.kernel.answer(KnowledgeAnswer(query='Memoria部署流程',
        memory_mode='bm25', use_adaptive_retrieval=False)))
    assert result['status'] == 'answer' and result['reader_called']
    assert calls == [f"[consolidation:{head['revision_id']}]"]
    trace = result['closed_loop']
    selected = next(e for e in trace['selected'] if e['kind'] == 'consolidated_memory')
    assert {r['id'] for r in selected['sources']} == {s['id'] for s in sources}
    assert len(trace['reader']['citations']) == 1
