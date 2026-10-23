"""Existing BLIP text is explicitly attributed; these are synthetic adapter tests."""
import asyncio

from memoria.experiments.eval_benchmarks import locomo_items
from memoria.experiments.eval_systems import source_batches, RawContextSystem
from memoria.memory.adaptive_retrieval import Sources
from memoria.memory.library import SourceInput
from memoria.memory.records import KnowledgeAnswer
from tests.test_memory_kernel import core

__all__ = ['core']


def dataset():
    return [{'sample_id': 'synthetic-caption', 'conversation': {
        'session_1_date_time': '8 May, 2023', 'session_1': [
            {'dia_id': 'D1:1', 'speaker': 'Alex', 'text': 'Here is my latest piece.',
             'blip_caption': '  A cobalt sailboat, with a red flag.\nA second line.  '},
            {'dia_id': 'D1:2', 'speaker': 'Blair', 'text': 'Thanks for sharing.', 'blip_caption': ''}]},
        'qa': [{'question': 'What is pictured?', 'answer': 'Sailboat', 'evidence': ['D1:1'], 'category': 4}]}]


def test_default_keeps_the_old_dialogue_only_input_and_reports_omitted_annotations():
    item = list(locomo_items(dataset()))[0]
    assert [s['id'] for s in item.sources] == ['synthetic-caption/D1:1', 'synthetic-caption/D1:2']
    assert item.sources[0]['text'] == 'Here is my latest piece.'
    assert 'cobalt' not in str(item.sources)
    assert item.extras['source_input_policy'] == 'locomo-dialogue-text-v1'
    assert item.extras['source_input_audit'] == {'dialogue_turns': 2, 'nonempty_blip_captions': 1,
        'imported_blip_captions': 0, 'excluded_blip_captions': 1}


def test_opt_in_caption_is_a_separate_machine_source_with_exact_caption_and_unknown_event_time():
    raw = dataset()
    item = list(locomo_items(raw, include_captions=True))[0]
    parent, caption, reply = item.sources
    assert parent['text'] == raw[0]['conversation']['session_1'][0]['text']
    assert caption['id'] == parent['id'] + '#blip-caption'
    assert caption['parent_source_id'] == parent['id']
    assert caption['speaker'] == 'machine image caption (BLIP)' and caption['speaker'] != parent['speaker']
    assert caption['source_kind'] == 'machine_image_caption'
    assert caption['session'] == parent['session'] and caption['session_date_raw'] == ''
    assert caption['text'].endswith('\n' + raw[0]['conversation']['session_1'][0]['blip_caption'])
    assert '"original_utterance": false' in caption['text']
    assert 'not caption authorship or verified identity' in caption['text']
    assert '"event_time": "unknown"' in caption['text']
    assert '8 May, 2023' not in caption['text']
    assert item.extras['source_input_policy'] == 'locomo-dialogue-and-blip-caption-v2'
    assert item.extras['source_input_audit']['imported_blip_captions'] == 1
    assert item.extras['source_input_audit']['excluded_blip_captions'] == 0


def test_caption_attribution_survives_production_batching_and_gate_quote_catalog(core):
    item = list(locomo_items(dataset(), include_captions=True))[0]
    for batch in source_batches(item.sources):
        core.ingest(SourceInput(kind='note', authorship='external', title='Synthetic caption input',
            content=batch['content'], source_units=batch['source_units']))
    adapter = Sources(core, KnowledgeAnswer(query='cobalt sailboat', memory_mode='bm25',
        include_formed_memory=False, budget_chars=16000))
    access = asyncio.run(adapter.retrieve())
    caption = next(c for c in access['candidates'] if c['data']['provenance'].get('source_unit', {}).get('id', '').endswith('#blip-caption'))
    unit = caption['data']['provenance']['source_unit']
    assert unit['speaker'] == 'machine image caption (BLIP)'
    assert unit['session_date_raw'] == '' and unit['identity_verified'] is False
    _, _, evidence, _ = adapter.present(access, [caption])
    assert evidence[0]['session_date_raw'] == ''
    assert evidence[0]['quote_fields'] == ['text']
    assert '"event_time": "unknown"' in evidence[0]['text']
    assert '"original_utterance": false' in evidence[0]['text']
    baseline = RawContextSystem(None, system='Synthetic reader')
    messages = baseline._messages('What is pictured?', item.sources)
    assert 'machine image caption (BLIP)' in messages[1]['content']
    assert 'cobalt sailboat' in messages[1]['content']


def test_gold_does_not_influence_caption_input():
    first = dataset()
    second = dataset()
    second[0]['qa'][0].update(answer='a different gold answer', evidence=['D999:99'])
    assert list(locomo_items(first, include_captions=True))[0].sources == list(locomo_items(second, include_captions=True))[0].sources
