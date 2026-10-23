"""Synthetic engineering checks for derivation adjudication, citation augmentation,
dense supplement probe and the inference-marked knowledge fallback. Not quality labels."""
import asyncio
import json

import pytest

from memoria.config import Settings
from memoria.store import Store
from memoria.memory.kernel import MemoryKernel
from memoria.memory.access import QueryRequest
from memoria.memory.library import SourceInput
from memoria.memory.records import KnowledgeAnswer
from memoria.memory.adaptive_retrieval import GATE
from memoria.memory.evidence.derivation_review import DERIVE_SYSTEM, validate as validate_derivation
from memoria.memory.knowledge_fallback import SYSTEM as FALLBACK_SYSTEM, verdict as fallback_verdict
from memoria.memory.answer_risk import SYSTEM as SUPPORT
from memoria.llm.types import ChatResult
from memoria.prompting.assembler import _parse_frame
from tests.test_memory_kernel import config_at

TURN_CLOCK = '4:04 pm on 20 April, 2023'
TURN_TEXT = "I've started expanding my studio's social media presence this month!"
QUESTION = "When did Jon start expanding his studio's social media presence?"


def library_core(tmp_path):
    from memoria.memory.source_units import render_turns
    settings = Settings.load(config_at(tmp_path / 'config'))
    store = Store(settings.database)
    model = FallbackModel(settings)
    kernel = MemoryKernel(settings, store, model)
    session_id = store.create_session('Synthetic source session')['id']
    content, units = render_turns([{'id': 'conv-30/D1:1', 'speaker': 'Jon',
                                    'session_date_raw': TURN_CLOCK, 'text': TURN_TEXT}])
    kernel.ingest(SourceInput(kind='note', authorship='external', scope='working',
        title='Synthetic public session', session_id=session_id, content=content,
        source_units=units, origin='engineering:synthetic'))
    yield kernel, model, session_id
    store.close()


class FallbackModel:
    """Gate always missing; focused derivation/fallback handlers; plain reader/support."""

    def __init__(self, settings, *, derive='supported', fallback='answer'):
        from memoria.prompting.reader_budget import ReaderBudget
        self.settings, self.reader_budget = settings, ReaderBudget(settings)
        self.derive, self.fallback = derive, fallback
        self.calls, self.gates, self.derivations, self.fallbacks = [], 0, 0, 0
        self.undated = False
        self.reader_abstain = False
        self.gate_sufficient = False

    async def chat(self, messages, **kwargs):
        system = messages[0]['content']
        self.calls.append(system[:80])
        if GATE in system:
            payload = json.loads(messages[1]['content'])
            self.gates += 1
            if self.gate_sufficient:
                matched = payload['evidence'][0]
                value = {'state': 'sufficient', 'requirements': [
                    {'fact': r['fact'], 'kind': 'fact', 'status': 'supported',
                     'sources': [{'id': matched['id'], 'quote': matched['source_texts'][0]}],
                     'binding': {'value': matched['source_texts'][0][:200], 'basis': 'explicit',
                                 'missing_components': []}}
                    for r in payload['query_plan']['requirements']],
                    'missing_queries': [], 'reason': 'Synthetic sufficient.'}
            else:
                requirements = [{'fact': r['fact'], 'kind': 'fact', 'status': 'missing', 'sources': [],
                                 'binding': None} for r in payload['query_plan']['requirements']]
                value = {'state': 'missing_fact', 'requirements': requirements,
                         'missing_queries': ['studio social media'], 'reason': 'Synthetic missing.'}
        elif DERIVE_SYSTEM in system:
            payload = json.loads(messages[1]['content'])
            self.derivations += 1
            pair = payload['pairs'][0]
            if self.derive == 'supported':
                text = pair['text'] if not self.undated else \
                    "I've started expanding my studio's social media presence"
                value = {'status': 'supported', 'value': 'April 2023',
                         'sources': [{'id': pair['id'], 'field': 'text', 'quote': text},
                                     {'id': pair['id'], 'field': 'session_date_raw', 'quote': pair['session_date_raw']}],
                         'reason': 'Event underway at the clock month.'}
            else:
                value = {'status': 'missing', 'value': '', 'sources': [], 'reason': 'Not established.'}
        elif FALLBACK_SYSTEM in system:
            self.fallbacks += 1
            return ChatResult(content=('Yes, someone who actively supports the community would usually be '
                                       'considered an ally.' if self.fallback == 'answer' else 'no information available'),
                              usage={'total_tokens': 20}, finish_reason='stop')
        elif SUPPORT in system:
            packet = json.loads(messages[1]['content'])
            e = packet['evidence'][0]
            value = {'verdict': 'supported', 'claims': [{'statement': packet['proposed_answer'],
                'relation': 'supported', 'basis': 'explicit',
                'sources': [{'id': e['id'], 'quote': e['source_texts'][0]}], 'reason': 'Synthetic.'}],
                'missing_queries': [], 'reason': 'Synthetic.'}
            if 'query_plan' in packet:
                value['requirement_coverage'] = [{'fact': r['fact'], 'status': 'answered',
                    'answer_units': [packet['proposed_answer']]} for r in packet['query_plan']['requirements']]
        else:
            _, sections = _parse_frame(messages[1]['content'])
            section = next(s for s in sections if s.name in {'Structured State', 'Source Evidence'})
            packet = json.loads(section.content.rsplit('\n', 1)[-1])
            ids = packet.get('allowed_evidence_ids') or [e['citation'] for e in packet['evidence']]
            if self.reader_abstain:
                value = {'status': 'abstain', 'answer': 'cannot determine', 'evidence_ids': []}
            else:
                value = {'status': 'answer', 'answer': 'April 2023', 'evidence_ids': ids[:1]}
        return ChatResult(content=json.dumps(value, ensure_ascii=False), usage={'total_tokens': 30},
                          finish_reason='stop')


def test_time_derivation_adjudication_converts_a_refusal(tmp_path):
    for kernel, model, session_id in library_core(tmp_path):
        result = asyncio.run(kernel.answer(KnowledgeAnswer(query=QUESTION, limit=4,
            session_id=session_id, budget_chars=16000, use_adaptive_retrieval=True)))
        assert result['status'] == 'answer', result
        trace = result['access']['adaptive']
        assert model.gates == 2 and model.derivations == 1
        assert trace['derivation_review']['accepted'] and trace['derivation_review']['bound_value'] == 'April 2023'
        assert result['citations'] and result['validation']['valid']


def test_undated_event_derivation_binds_via_same_id_clock(tmp_path):
    """An as-of derivation carries its date in the session clock, not the event text."""
    for kernel, model, session_id in library_core(tmp_path):
        kernel.llm.undated = True
        result = asyncio.run(kernel.answer(KnowledgeAnswer(query=QUESTION, limit=4,
            session_id=session_id, budget_chars=16000, use_adaptive_retrieval=True)))
        assert result['status'] == 'answer', result
        assert result['access']['adaptive']['derivation_review']['accepted']


def test_reader_abstain_on_inference_question_uses_fallback(tmp_path):
    for kernel, model, session_id in library_core(tmp_path):
        kernel.llm.reader_abstain = True
        kernel.llm.gate_sufficient = True
        result = asyncio.run(kernel.answer(KnowledgeAnswer(
            query='Would Jon consider expanding his studio social media further?',
            limit=4, session_id=session_id, budget_chars=16000, use_adaptive_retrieval=True)))
        assert result['status'] == 'answer' and model.fallbacks == 1
        assert result['access']['adaptive']['knowledge_fallback']['exit'] == 'reader_abstained'


def test_rejected_derivation_keeps_the_evidence_refusal(tmp_path):
    for kernel, model, session_id in library_core(tmp_path):
        model.derive = 'missing'
        result = asyncio.run(kernel.answer(KnowledgeAnswer(query=QUESTION, limit=4,
            session_id=session_id, budget_chars=16000, use_adaptive_retrieval=True)))
        assert result['status'] == 'abstain'
        assert result['validation']['reason'] == 'required_evidence_not_established'
        trace = result['access']['adaptive']
        assert trace['derivation_review']['accepted'] is False
        assert 'knowledge_fallback' not in trace  # factual when-question never falls back


def test_knowledge_fallback_only_for_inference_flavored_questions(tmp_path):
    for kernel, model, session_id in library_core(tmp_path):
        result = asyncio.run(kernel.answer(KnowledgeAnswer(query='Would Melanie be considered an ally?',
            limit=4, session_id=session_id, budget_chars=16000, use_adaptive_retrieval=True)))
        assert result['status'] == 'answer'
        assert model.fallbacks == 1 and not result['citations']
        assert result['validation']['knowledge_fallback'] and result['validation']['basis'] == 'inference'
        assert result['access']['adaptive']['knowledge_fallback']['accepted']


def test_knowledge_fallback_refusal_text_keeps_abstain(tmp_path):
    for kernel, model, session_id in library_core(tmp_path):
        model.fallback = 'refuse'
        result = asyncio.run(kernel.answer(KnowledgeAnswer(query='Would Melanie be considered an ally?',
            limit=4, session_id=session_id, budget_chars=16000, use_adaptive_retrieval=True)))
        assert result['status'] == 'abstain'
        # The original evidence refusal survives on whichever exit path it took.
        assert result['validation']['reason'] in {'required_evidence_not_established', 'no_bounded_live_candidates'}
        assert result['access']['adaptive']['knowledge_fallback']['accepted'] is False


def test_factual_question_without_inference_words_never_falls_back(tmp_path):
    for kernel, model, session_id in library_core(tmp_path):
        result = asyncio.run(kernel.answer(KnowledgeAnswer(query='What book is Gina currently reading?',
            limit=4, session_id=session_id, budget_chars=16000, use_adaptive_retrieval=True)))
        assert result['status'] == 'abstain' and model.fallbacks == 0
        assert 'knowledge_fallback' not in result['access']['adaptive']


def test_second_supplement_probe_runs_dense_lane_with_fused_fallback(tmp_path):
    for kernel, model, session_id in library_core(tmp_path):
        modes, original = [], kernel.queries.search

        async def spy(request, *, capture=None, retrieval_query=None):
            modes.append(request.memory_mode)
            return await original(request, capture=capture, retrieval_query=retrieval_query)

        kernel.queries.search = spy
        result = asyncio.run(kernel.answer(KnowledgeAnswer(query=QUESTION, limit=4,
            session_id=session_id, budget_chars=16000, use_adaptive_retrieval=True)))
        assert result['status'] == 'answer'
        assert modes[0] == 'rrf' and 'dense' in modes
        supplements = result['access']['adaptive']['supplements']
        assert len(supplements) == 2 and supplements[1].get('lane') == 'dense'


class MissingCitationModel(FallbackModel):
    """Supported gate over three distinct memories; reader cites only the first."""

    def __init__(self, settings):
        super().__init__(settings)
        self.reader_ids = None

    async def chat(self, messages, **kwargs):
        system = messages[0]['content']
        if GATE in system:
            payload = json.loads(messages[1]['content'])
            markers = {'project': '项目', 'adoption': '采用', 'reason': '因为', 'outcome': '问题'}
            requirements = []
            for r in payload['query_plan']['requirements']:
                key = r['fact'].split(':')[0]
                marker = markers.get(key, '')
                matched = next((e for e in payload['evidence'] if marker in e['text']), payload['evidence'][0])
                requirements.append({'fact': r['fact'], 'kind': 'fact', 'status': 'supported',
                    'sources': [{'id': matched['id'], 'quote': matched['source_texts'][0]}],
                    'binding': {'value': matched['source_texts'][0][:200], 'basis': 'explicit',
                                'missing_components': []}})
            value = {'state': 'sufficient', 'requirements': requirements, 'missing_queries': [],
                     'reason': 'Synthetic sufficient.'}
            return ChatResult(content=json.dumps(value, ensure_ascii=False), usage={'total_tokens': 30},
                              finish_reason='stop')
        if SUPPORT in system:
            packet = json.loads(messages[1]['content'])
            e = packet['evidence'][0]
            value = {'verdict': 'supported', 'claims': [{'statement': packet['proposed_answer'],
                'relation': 'supported', 'basis': 'explicit',
                'sources': [{'id': e['id'], 'quote': e['source_texts'][0]}], 'reason': 'Synthetic.'}],
                'missing_queries': [], 'reason': 'Synthetic.',
                'requirement_coverage': [{'fact': r['fact'], 'status': 'answered',
                    'answer_units': [packet['proposed_answer']]} for r in packet['query_plan']['requirements']]}
            return ChatResult(content=json.dumps(value, ensure_ascii=False), usage={'total_tokens': 30},
                              finish_reason='stop')
        _, sections = _parse_frame(messages[1]['content'])
        section = next(s for s in sections if s.name in {'Structured State', 'Source Evidence'})
        packet = json.loads(section.content.rsplit('\n', 1)[-1])
        ids = packet.get('allowed_evidence_ids') or [e['citation'] for e in packet['evidence']]
        value = {'status': 'answer', 'answer': '演练项目采用快照方案', 'evidence_ids': ids[:1]}
        return ChatResult(content=json.dumps(value, ensure_ascii=False), usage={'total_tokens': 30},
                          finish_reason='stop')


MULTI_QUERY = '我之前在哪个项目采用了快照方案？当时为什么选择它？后来遇到了什么问题？'
MEMORIES = ['演练项目采用了快照方案', '选择它因为恢复快', '后来遇到磁盘空间不足的问题']


def citation_core(tmp_path):
    settings = Settings.load(config_at(tmp_path / 'config'))
    store = Store(settings.database)
    model = MissingCitationModel(settings)
    kernel = MemoryKernel(settings, store, model)
    session = store.create_session('Synthetic only')['id']
    for text in MEMORIES:
        msg = store.add_message(session, 'user', text)
        store.add_memory(text, source_ref=msg['id'], require_source=True)
    yield kernel, model
    store.close()


def test_subset_citations_are_augmented_instead_of_discarding(tmp_path):
    for kernel, model in citation_core(tmp_path):
        result = asyncio.run(kernel.answer(QueryRequest(query=MULTI_QUERY, mode='rrf',
            include_experiences=False, use_adaptive_retrieval=True, budget_chars=16000)))
        assert result['status'] == 'answer', result
        trace = result['access']['adaptive']
        assert trace['citation_augmentation']['added'], trace.get('citation_augmentation')
        cited = {c['id'] for c in result['citations']}
        required = {q['id'] for r in trace['rounds'][-1]['decision']['requirements'] for q in r['sources']}
        assert required <= cited


PAIR = {'id': 'unit-1', 'text': TURN_TEXT, 'session_date_raw': TURN_CLOCK}


def test_clock_pairs_keep_each_items_own_clock():
    """A shared leftover loop clock once mislabeled every offered pair."""
    from memoria.memory.evidence.derivation_review import clock_pairs
    april = {'id': 'unit-april', 'text': 'I started expanding my studio social media this month.',
             'session_date_raw': '1:26 pm on 3 April, 2023', 'speaker': 'Jon',
             'source_texts': ['I started expanding my studio social media this month.']}
    january = {'id': 'unit-jan', 'text': 'Already posting dance videos online.',
               'session_date_raw': '4:04 pm on 20 January, 2023', 'speaker': 'Jon',
               'source_texts': ['Already posting dance videos online.']}
    pairs = clock_pairs([april, january], {'lexical_terms': ['studio', 'social', 'media']})
    clocks = {pair['id']: pair['session_date_raw'] for pair in pairs}
    assert clocks == {'unit-april': '1:26 pm on 3 April, 2023',
                      'unit-jan': '4:04 pm on 20 January, 2023'}


@pytest.mark.parametrize('mutation', ['foreign_id', 'cross_clock', 'partial_clock', 'no_text'])
def test_derivation_contract_violations_are_rejected(mutation):
    sources = [{'id': 'unit-1', 'field': 'text', 'quote': TURN_TEXT[:40]},
               {'id': 'unit-1', 'field': 'session_date_raw', 'quote': TURN_CLOCK}]
    if mutation == 'foreign_id':
        sources[0]['id'] = sources[1]['id'] = 'unit-9'
    elif mutation == 'cross_clock':
        sources[1] = {'id': 'unit-2', 'field': 'session_date_raw', 'quote': TURN_CLOCK}
    elif mutation == 'partial_clock':
        sources[1]['quote'] = '20 April, 2023'
    elif mutation == 'no_text':
        sources = [sources[1]]
    value = {'status': 'supported', 'value': 'April 2023', 'sources': sources, 'reason': 'Synthetic.'}
    with pytest.raises(ValueError):
        validate_derivation(value, {'fact': 'detail_1: time', 'lexical_terms': ['studio']}, [PAIR])


def test_fallback_verdict_rejects_refusals_and_json_echoes():
    assert fallback_verdict('no information available') is None
    assert fallback_verdict('') is None
    assert fallback_verdict('{"status":"answer"}') is None
    accepted = fallback_verdict('Yes, she would be considered an ally.')
    assert accepted['status'] == 'answer' and accepted['validation']['basis'] == 'inference'
