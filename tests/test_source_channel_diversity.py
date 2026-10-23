"""Synthetic channel contracts; no benchmark gold, remote calls or user data."""
import asyncio
import json

from memoria.memory.embedding import DocumentEmbedding
from memoria.memory.source_fusion import choose
from memoria.memory.records import MemorySearch
from memoria.memory.source_units import render_turns
from tests.test_library_hybrid import core, add

__all__ = ['core']


def test_unique_high_dense_rank_survives_consensus_cropping_at_the_same_limit():
    ordered = list(range(1, 17))
    lanes = {'keyword': ordered, 'bm25': ordered, 'dense': [1, 2, 3, 16, *range(4, 16)]}
    selected, reasons = choose(ordered, lanes, 8)
    assert len(selected) == len(set(selected)) == 8
    assert selected[0] == 1 and selected[1] == 16
    assert reasons[16] == {'fused_rank': 16, 'selection_rank': 2,
                           'selection_reason': 'channel_prefix', 'protected_channels': ['dense']}
    assert set(ordered[:4]) <= set(selected)
    assert (selected, reasons) == choose(ordered, lanes, 8)


def test_prefix_depth_is_bounded_and_does_not_protect_arbitrary_tail_hits():
    ordered = list(range(1, 17))
    selected, _ = choose(ordered, {'keyword': ordered, 'dense': [1, 2, 3, 4, 16]}, 8)
    assert selected == ordered[:8]
    assert 16 not in selected


def test_single_mode_empty_and_duplicate_lanes_keep_rank_and_capacity():
    ordered = list(range(1, 10))
    assert choose(ordered, {'dense': ordered}, 4)[0] == ordered[:4]
    assert choose(ordered, {'keyword': ordered, 'dense': []}, 4)[0] == ordered[:4]
    assert choose(ordered, {'keyword': ordered * 2, 'dense': ordered}, 4)[0] == ordered[:4]
    assert choose([], {'dense': []}, 8) == ([], {})
    assert choose(ordered, {'dense': ordered}, 0) == ([], {})


def test_live_filter_and_multiple_complementary_channels_are_respected():
    ordered = list(range(1, 17))
    lanes = {'keyword': [1, 2, 3, 4], 'bm25': [1, 2, 15, 14],
             'dense': [999, 1, 16, 13]}
    selected, reasons = choose(ordered, lanes, 8)
    assert {15, 16} <= set(selected)
    assert 999 not in selected and len(selected) == 8
    assert reasons[15]['protected_channels'] == ['bm25']
    assert reasons[16]['protected_channels'] == ['dense']


class ComplementaryEncoder:
    enabled = True
    is_local = True
    namespace = 'synthetic-complementary-space'

    async def embed_documents(self, texts):
        return [DocumentEmbedding([1., 0.] if 'semantic-vector-target' in t else [.2, 1.]) for t in texts]

    async def embed_query(self, text):
        return [1., 0.]


def test_native_library_capture_preserves_complement_and_separate_bounded_diagnostics(core):
    target = add(core, 'semantic-vector-target information.')
    for index in range(18):
        add(core, f'Storage background discussion number {index}.')
    core.memory.embedder = ComplementaryEncoder()
    capture = {}
    packet = asyncio.run(core.search(MemorySearch(query='storage', domains=['library'],
        memory_mode='rrf', limit=8, include_formed_memory=False), capture=capture))
    candidates = capture['candidates']
    hit = next(c for c in candidates if c['source_id'] == target['id'])
    diagnostic = capture['retrieval']['library']
    selected = next(s for s in diagnostic['candidate_selection']['selected'] if s['id'] == hit['citation'])
    assert selected['selection_reason'] == 'channel_prefix' and selected['fused_rank'] > 8
    assert selected['protected_channels'] == ['dense']
    assert len(candidates) == 8 and diagnostic['selection_limit'] == 8
    assert diagnostic['dropped_by_limit'] == 11
    assert diagnostic['candidate_selection']['semantic_sufficiency_verified'] is False
    assert diagnostic['dense_available'] and not diagnostic['fallback']
    public_diagnostic = packet.get('retrieval', {}).get('library', {})
    assert 'candidate_selection' not in public_diagnostic
    assert 'lanes' not in public_diagnostic
    assert packet['budget']['used'] <= packet['budget']['limit']


def test_pronoun_candidate_can_reach_two_turn_antecedent_without_claiming_coreference(core):
    text, units = render_turns([
        {'id': 'antecedent', 'speaker': 'Alex', 'text': 'I made a cobalt glass lantern.'},
        {'id': 'question', 'speaker': 'Blair', 'text': 'Who was the recipient?'},
        {'id': 'target', 'speaker': 'Alex', 'text': 'It was made for the village hall.'},
        {'id': 'reply', 'speaker': 'Blair', 'text': 'That sounds thoughtful.'}])
    add(core, text, source_units=units)
    capture = {}
    asyncio.run(core.search(MemorySearch(query='village hall', domains=['library'],
        memory_mode='bm25', limit=1, include_formed_memory=False), capture=capture))
    candidates = capture['candidates']
    assert {c['provenance']['source_unit']['id'] for c in candidates} == {
        'antecedent', 'question', 'target', 'reply'}
    edges = capture['retrieval']['library']['adjacency_hints']
    assert {e['turn_offset'] for e in edges} == {-1, -2, 1}
    assert all(e['semantic_link_verified'] is False for e in edges)
    assert len(candidates) <= 5


def test_two_turn_lookback_is_not_a_general_history_expansion(core):
    text, units = render_turns([
        {'id': 'far', 'speaker': 'Alex', 'text': 'Separate older discussion.'},
        {'id': 'previous', 'speaker': 'Blair', 'text': 'Another unrelated question.'},
        {'id': 'target', 'speaker': 'Alex', 'text': 'Alex made a chair for the village hall.'},
        {'id': 'next', 'speaker': 'Blair', 'text': 'Thanks for explaining.'}])
    add(core, text, source_units=units)
    capture = {}
    asyncio.run(core.search(MemorySearch(query='village hall', domains=['library'],
        memory_mode='bm25', limit=1, include_formed_memory=False), capture=capture))
    assert {c['provenance']['source_unit']['id'] for c in capture['candidates']} == {'previous', 'target', 'next'}
    assert all(e['turn_offset'] in {-1, 1} for e in capture['retrieval']['library']['adjacency_hints'])


def test_optional_diagnostics_never_displace_original_evidence_or_dependencies(core, monkeypatch):
    add(core, 'Storage recovery uses snapshots.')
    request = MemorySearch(query='storage', domains=['library'], memory_mode='bm25', budget_chars=4000)
    baseline = asyncio.run(core.search(request))
    assert baseline['evidence']
    native_search = core.queries.sources.search

    async def verbose_search(*args, **kwargs):
        hits, diagnostic = await native_search(*args, **kwargs)
        diagnostic['synthetic_verbose_optional_diagnostic'] = 'x' * 5000
        return hits, diagnostic

    monkeypatch.setattr(core.queries.sources, 'search', verbose_search)
    capture = {}
    result = asyncio.run(core.search(request, capture=capture))
    assert result['evidence'] == baseline['evidence']
    assert result['dependencies'] == baseline['dependencies']
    assert result['selection']['skipped_budget'] == baseline['selection']['skipped_budget']
    assert 'retrieval' not in result
    assert capture['retrieval']['library']['synthetic_verbose_optional_diagnostic'] == 'x' * 5000
    assert result['budget']['used'] == len(json.dumps(result, ensure_ascii=False))
    assert result['budget']['used'] <= 4000
