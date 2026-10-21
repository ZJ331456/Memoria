"""Isolated synthetic engineering checks; not retrieval-quality/longitudinal labels."""
import asyncio
import copy
import json

import pytest

from memoria.config import Settings
from memoria.store import Store
from memoria.memory.kernel import MemoryKernel
from memoria.memory.access import QueryRequest, infer_query
from memoria.memory.library import SourceInput
from memoria.memory.records import KnowledgeAnswer
from memoria.memory.evidence.query_plan import plan, validate_plan, complex_recall
from memoria.memory.evidence.closure import select_closed
from memoria.memory.evidence.budget import EvidenceBudget
from memoria.memory.adaptive_retrieval import GATE, merge_candidates
from memoria.memory.answer_risk import SYSTEM as SUPPORT
from memoria.llm.types import ChatResult
from memoria.prompting.reader_budget import ReaderBudget
from memoria.prompting.assembler import _parse_frame
from tests.test_memory_kernel import config_at
from tests.test_reader_contract import core as app_core

QUERY = '我之前在哪个项目采用了快照方案？当时为什么选择它？后来遇到了什么问题？'
TEXT = '演练项目采用了快照方案，因为恢复快，后来遇到了磁盘空间不足的问题。'


class Model:
    def __init__(self, settings, *, missing=0, broken=None, mutate=None):
        self.settings, self.reader_budget = settings, ReaderBudget(settings)
        self.missing, self.broken, self.mutate = missing, broken, mutate
        self.calls, self.gates, self.evidence = [], 0, []

    async def chat(self, messages, **kwargs):
        system = messages[0]['content']
        self.calls.append(system)
        if system.startswith(GATE):
            payload = json.loads(messages[1]['content'])
            self.gates += 1
            self.evidence = payload['evidence']
            quote = self.evidence[0]
            missing = self.gates <= self.missing
            requirements = []
            for i, r in enumerate(payload['query_plan']['requirements']):
                marker = {'project': '项目', 'adoption': '采用', 'reason': '因为', 'outcome': '问题'}.get(r['fact'].split(':')[0], '')
                matched = next((e for e in self.evidence if marker in e['text']), None)
                unavailable = not matched or (missing and i == len(payload['query_plan']['requirements']) - 1)
                requirements.append({'fact': r['fact'], 'kind': 'fact', 'status': 'missing' if unavailable else 'supported',
                    'sources': [] if unavailable else [{'id': matched['id'], 'quote': matched['source_texts'][0]}]})
            missing = any(r['status'] == 'missing' for r in requirements)
            value = {'state': 'missing_fact' if missing else 'sufficient', 'requirements': requirements,
                'missing_queries': ['磁盘空间问题'] if missing else [], 'reason': 'Synthetic evidence check.'}
            if self.broken == 'drop': value['requirements'].pop()
            if self.broken == 'quote': value['requirements'][0]['sources'][0]['quote'] = 'fabricated evidence'
            if self.broken == 'id': value['requirements'][0]['sources'][0]['id'] = 'foreign'
        elif system.startswith(SUPPORT):
            packet = json.loads(messages[1]['content'])
            e = packet['evidence'][0]
            value = {'verdict': 'supported', 'claims': [{'statement': packet['proposed_answer'], 'relation': 'supported',
                'basis': 'explicit', 'sources': [{'id': e['id'], 'quote': e['source_texts'][0]}], 'reason': 'Synthetic.'}],
                'missing_queries': [], 'reason': 'Synthetic.'}
        else:
            _, sections = _parse_frame(messages[1]['content'])
            section = next(s for s in sections if s.name in {'Structured State', 'Source Evidence'})
            packet = json.loads(section.content.rsplit('\n', 1)[-1])
            ids = packet.get('allowed_evidence_ids') or [e['citation'] for e in packet['evidence']]
            value = {'status': 'answer', 'answer': TEXT, 'evidence_ids': ids}
            if self.broken == 'citation': value['evidence_ids'] = ['foreign']
        if self.mutate: self.mutate(len(self.calls))
        return ChatResult(content=json.dumps(value, ensure_ascii=False), usage={'total_tokens': 30}, finish_reason='stop')


@pytest.fixture
def core(tmp_path):
    settings = Settings.load(config_at(tmp_path / 'config'))
    store = Store(settings.database)
    model = Model(settings)
    kernel = MemoryKernel(settings, store, model)
    session = store.create_session('Synthetic only')['id']
    msg = store.add_message(session, 'user', TEXT)
    store.add_memory(TEXT, source_ref=msg['id'], require_source=True)
    yield kernel, model, msg
    store.close()


def test_plan_is_query_only_and_cannot_drop_a_hop():
    p = plan(QUERY, {})
    assert len(p['requirements']) == 4 and p['unknowns_preserved'] and not p['gold_used']
    assert p['subject_id'] is None and p['time_precision'] == 'unknown'
    assert complex_recall(QUERY) and infer_query(QUERY).use_adaptive_retrieval
    assert infer_query('我今天肚子疼，为什么？') is None
    assert QueryRequest(query='a').use_llm_rerank is False
    assert KnowledgeAnswer(query='a').use_llm_rerank is False


def test_posthoc_scenario_counter_accepts_native_source_citations(core):
    from memoria.experiments.simulation import observations_at
    kernel, _, _ = core
    native = {'status': 'completed', 'result': {'access': {'candidates': [{'citation':'[library:old:0:3]'}]}}}
    assert observations_at(native, kernel.settings.database)['adaptive_new_candidates'] == 0
    native['result']['access']['adaptive'] = {'enabled':True, 'initial_candidates':[]}
    assert observations_at(native, kernel.settings.database)['adaptive_new_candidates'] == 1


def test_source_coverage_quotes_original_support_not_consolidated_summary(core):
    from memoria.memory.adaptive_retrieval import Sources
    kernel, _, _ = core
    candidate = {'id':'[consolidation:x]','data':{'representation':'consolidated_memory','content':'Unverified model summary',
        'citation':'[consolidation:x]','provenance':{'support':[{'quote':'Original event','subject_quote':'Original identity','feedback_quote':'Original outcome'}]}},
        'dependency':{}}
    _, _, evidence, _ = Sources(kernel, KnowledgeAnswer(query=QUERY)).present({}, [candidate])
    assert evidence[0]['source_texts'] == ['Original event','Original identity','Original outcome']
    assert 'Unverified model summary' not in evidence[0]['text']


@pytest.mark.parametrize('kind', ['personal', 'library'])
@pytest.mark.parametrize('missing', [0, 1, 9])
def test_actual_kernel_gate_supplement_reader_support_and_hard_stop(core, kind, missing):
    kernel, model, msg = core
    model.missing = missing
    if kind == 'library':
        kernel.ingest(SourceInput(kind='document', title='Synthetic project', content=TEXT, authorship='external'))
        request = KnowledgeAnswer(query=QUERY, use_adaptive_retrieval=True, budget_chars=16000)
    else:
        request = QueryRequest(query=QUERY, mode='rrf', include_experiences=False, use_adaptive_retrieval=True, budget_chars=16000)
    result = asyncio.run(kernel.answer(request))
    trace = result['access']['adaptive']
    assert trace['retrieval_rounds'] == int(missing > 0)
    assert model.gates == (2 if missing else 1)
    assert len(model.calls) <= 7 and trace['policy']['reserved_calls'] == 0
    assert trace['policy']['input_bytes'] <= trace['policy']['max_input_bytes']
    assert not trace['llm_rerank_enabled'] and not trace['full_history_reader_allowed']
    assert result['status'] == ('abstain' if missing > 1 else 'answer'), result
    if result['status'] == 'answer':
        assert result['reader_called'] and result['validation']['valid'] and result['citations']
        assert len(trace['rounds'][-1]['decision']['requirements']) == 4
        if kind == 'personal' and not missing:
            assert not trace['rounds'][0]['initially_recalled_but_not_selected']
    assert len(trace.get('supplements', [])) <= 2


@pytest.mark.parametrize('broken', ['drop', 'quote', 'id', 'citation'])
def test_bad_contracts_never_publish(core, broken):
    kernel, model, _ = core
    model.broken = broken
    result = asyncio.run(kernel.answer(QueryRequest(query=QUERY, use_adaptive_retrieval=True, include_experiences=False, budget_chars=16000)))
    assert result['status'] == 'abstain' and not result['citations']
    assert len(model.calls) <= 3


@pytest.mark.parametrize('stage', [1, 2, 3])
def test_withdrawal_at_any_model_boundary_stops_publication(core, stage):
    kernel, model, msg = core
    model.mutate = lambda n: kernel.store.undo_memory_sources([msg['id']]) if n == stage else None
    result = asyncio.run(kernel.answer(QueryRequest(query=QUERY, use_adaptive_retrieval=True, include_experiences=False, budget_chars=16000)))
    assert result['status'] == 'abstain' and not result['citations']


def test_no_budget_no_calls_and_no_implicit_reranker(core):
    kernel, model, _ = core
    kernel.settings.reader_input_bytes = 256
    result = asyncio.run(kernel.answer(QueryRequest(query=QUERY, use_adaptive_retrieval=True)))
    assert not model.calls and result['status'] == 'abstain'
    with pytest.raises(Exception):
        asyncio.run(kernel.answer(QueryRequest(query=QUERY, use_adaptive_retrieval=True, use_llm_rerank=True)))


def test_closure_never_drops_identity_or_cross_fact_prerequisite():
    candidates = [{'id': 'fact', 'data': {'text': 'fact'}, 'requires': ['identity']},
                  {'id': 'identity', 'data': {'text': 'identity'}}]
    decision = {'state': 'sufficient', 'requirements': [{'fact': 'r0', 'status': 'supported', 'sources': [{'id': 'citation'}]}]}
    result = select_closed(candidates, decision, {'citation': ['fact']}, EvidenceBudget(1000))
    assert [c['id'] for c in result['payload']['evidence']] == ['fact', 'identity']
    for budget, limit in [(EvidenceBudget(40), 8), (EvidenceBudget(1000), 1)]:
        result = select_closed(candidates, decision, {'citation': ['fact']}, budget, limit=limit)
        assert not result['fits'] and not result['payload']['evidence']
    candidates[1]['requires'] = ['fact']
    assert select_closed(candidates, decision, {'citation': ['fact']}, EvidenceBudget(1000))['complete']
    candidates[1]['requires'] = ['missing']
    with pytest.raises(ValueError): select_closed(candidates, decision, {'citation': ['fact']}, EvidenceBudget(1000))


def test_merge_retains_new_windows_and_rejects_mutation():
    old = [{'id': 'a', 'data': {'text': 'literal', 'retrieval': {'score': 1}}}]
    assert len(merge_candidates(old, [{'id': 'a', 'data': {'text': 'literal', 'retrieval': {'score': 2}}}])) == 1
    with pytest.raises(ValueError): merge_candidates(old, [{'id': 'a', 'data': {'text': 'changed'}}])


@pytest.mark.parametrize('empty', [False, True])
def test_targeted_locator_actually_adds_an_absent_candidate(core, empty):
    kernel, model, msg = core
    initial = kernel.memory.retrieve
    calls = []
    async def retrieve(query, *args, **kwargs):
        calls.append(query)
        rows = await initial(query if len(calls) == 1 else '演练项目', *args, **kwargs)
        return [] if empty and len(calls) == 1 else rows
    kernel.memory.retrieve = retrieve
    model.missing = 0 if empty else 1
    result = asyncio.run(kernel.answer(QueryRequest(query=QUERY, use_adaptive_retrieval=True, include_experiences=False, budget_chars=16000)))
    assert result['status'] == 'answer', result
    assert len(calls) == (3 if empty else 2) and calls[0] == QUERY and all(q != QUERY for q in calls[1:])
    assert result['access']['adaptive']['policy']['retrieval_rounds'] == 1
    if empty:
        assert not result['access']['adaptive']['rounds'][0]['candidate_ids']
        assert len(result['access']['adaptive']['rounds'][1]['candidate_ids']) == 1


def test_selected_budget_gap_is_not_reported_as_absent_recall(core):
    kernel, model, msg = core
    result = asyncio.run(kernel.answer(QueryRequest(query=QUERY, use_adaptive_retrieval=True, include_experiences=False, evidence_budget=256)))
    assert result['status'] == 'abstain' and not result['reader_called']
    assert result['validation']['reason'] == 'necessary_group_over_budget'
    assert result['access']['adaptive']['retrieval_rounds'] == 0
    assert not result['access']['adaptive']['rounds'][0]['not_supported_in_assessed_pool']
    assert result['access']['adaptive']['rounds'][0]['initially_recalled_but_not_selected']


def test_frozen_closure_replay_preserves_groups_and_detects_advice_changes(core):
    from memoria.observability.memory_trace import replay
    kernel, _, _ = core
    result = asyncio.run(kernel.answer(QueryRequest(query=QUERY, use_adaptive_retrieval=True, budget_chars=16000, include_experiences=False)))
    packet = result['access']
    frozen = replay(packet, kernel.claims.user_id)
    assert frozen['model_calls'] == 0
    assert frozen['selections']['16000']['dependency_closed']['payload'] == packet['selection']['payload']
    packet['closure_advice']['candidate_ids'] = []
    with pytest.raises(ValueError): replay(packet, kernel.claims.user_id)


@pytest.mark.parametrize('hook', ['none', 'withdraw', 'rewrite'])
def test_chat_and_bot_runtime_use_the_kernel_loop_and_final_hooks(app_core, hook):
    from memoria.lifecycle import Phase
    store, service, sid, _ = app_core
    msg = store.add_message(sid, 'user', TEXT)
    store.add_memory(TEXT, source_ref=msg['id'], require_source=True)
    model = Model(service.llm.settings)
    service.llm.chat = model.chat
    events = []
    async def event(value): events.append(value)
    async def after(context):
        if hook == 'withdraw': store.undo_memory_sources([msg['id']])
        if hook == 'rewrite': context.response = 'unverified hook replacement'
    service.runtime.pipeline.register(Phase.AFTER_REASONING, after)
    message, _, trace = asyncio.run(service.runtime.run(sid, QUERY, on_event=event, skip_memory_enqueue=True))
    assert trace['metadata']['adaptive_retrieval']['enabled']
    assert len(model.calls) == 3 and trace['metadata']['query_route'] == 'personal_memory'
    assert [e['content'] for e in events if e['type'] == 'delta'] == [message['content']]
    assert (message['content'] == TEXT) == (hook == 'none')
    assert bool(trace['metadata']['citations']) == (hook == 'none')


def test_bounded_llm_provider_does_not_hide_transport_retries(core):
    from memoria.llm import LLMClient
    from memoria.llm.call_scope import inference_scope
    kernel, _, _ = core
    kernel.settings.max_retries = 3
    client = LLMClient(kernel.settings)
    assert client.backend_for().max_retries == 3
    with inference_scope(lambda payload: {}):
        assert client.backend_for().max_retries == 0
    assert client.backend_for().max_retries == 3


@pytest.mark.parametrize('location', ['query', 'metadata'])
def test_secret_checks_cover_query_and_full_reader_metadata_before_any_model(core, location):
    kernel, model, _ = core
    secret = 'api_key: ' + 'synthetic-not-a-credential'
    if location == 'query':
        async def forbidden(*args, **kwargs): raise AssertionError('Credential query must not reach retrieval')
        kernel.memory.retrieve = forbidden
        request = QueryRequest(query=QUERY + secret, use_adaptive_retrieval=True)
    else:
        kernel.ingest(SourceInput(kind='document', title=secret, content=TEXT, authorship='self'))
        request = KnowledgeAnswer(query=QUERY, use_adaptive_retrieval=True)
    result = asyncio.run(kernel.answer(request))
    assert result['status'] == 'abstain' and not model.calls and not result['citations']
    assert result['validation']['reason'].startswith('credential_bearing')


def test_wall_timeout_stops_a_pending_model_and_retains_unknown_usage(core):
    kernel, model, _ = core
    kernel.settings.request_timeout_seconds = .02
    async def waiting(*args, **kwargs): await asyncio.sleep(1)
    kernel.llm.chat = waiting
    result = asyncio.run(kernel.answer(QueryRequest(query=QUERY, use_adaptive_retrieval=True, include_experiences=False)))
    assert result['status'] == 'abstain' and not result['reader_called']
    trace = result['access']['adaptive']
    assert trace['error_type'] == 'TimeoutError' and trace['unknown_usage_count'] == 1
    assert trace['policy']['calls'] == 1 and trace['policy']['reserved_calls'] == 0


@pytest.mark.parametrize('kind', ['personal','library'])
@pytest.mark.parametrize('issue', ['malformed','length'])
@pytest.mark.parametrize('always', [False,True])
def test_reader_format_regenerates_once_inside_complete_shared_budget(core, kind, issue, always):
    kernel, model, _ = core
    original = model.chat
    attempts = []
    async def chat(messages, **kwargs):
        system = messages[0]['content']
        if not system.startswith((GATE, SUPPORT)):
            attempts.append(system)
            if always or len(attempts)==1:
                model.calls.append(system)
                return ChatResult(content='{"status":"answer",broken', finish_reason='length' if issue=='length' else 'stop', usage={'total_tokens':4})
        return await original(messages, **kwargs)
    kernel.llm.chat = chat
    if kind=='library':
        kernel.ingest(SourceInput(kind='document',title='Synthetic',content=TEXT,authorship='self'))
        request=KnowledgeAnswer(query=QUERY,use_adaptive_retrieval=True,budget_chars=16000)
    else:request=QueryRequest(query=QUERY,use_adaptive_retrieval=True,budget_chars=16000,include_experiences=False)
    result=asyncio.run(kernel.answer(request))
    assert len(attempts)==2 and result['status']==('abstain' if always else 'answer'),result
    trace=result['access']['adaptive']
    assert len([c for c in trace['calls'] if c['stage']=='reader'])==2
    assert trace['policy']['max_calls']==8 and trace['policy']['calls']<=8 and trace['policy']['reserved_calls']==0
    assert trace['contract_errors'][0]['stage']=='reader'
    assert sum((u or {}).get('total_tokens',0) for u in result['risk_checks']['usage'])+(result['usage'] or {}).get('total_tokens',0)==sum(c['usage']['total_tokens'] for c in trace['calls'])
@pytest.mark.parametrize('fail', [False, True])
def test_embedding_requests_are_observed_without_hidden_retries(core, monkeypatch, fail):
    import httpx
    from memoria.config import ModelConfig
    from memoria.memory.embedding import EmbeddingClient, EmbeddingError
    from memoria.llm.call_scope import inference_scope
    calls, receipts = [], []
    def response(request):
        calls.append(request)
        return httpx.Response(503) if fail else httpx.Response(200, json={'data': [{'index':0,'embedding':[1., 0.]}], 'usage': {'total_tokens': 3}})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs))
    embedding = EmbeddingClient(ModelConfig(model='engineering', base_url='https://embedding.invalid/v1', api_key='x'), max_retries=3)
    def observed(payload):
        receipt = {'usage': None};receipts.append(receipt);return receipt
    with inference_scope(observed):
        if fail:
            with pytest.raises(EmbeddingError): asyncio.run(embedding.embed('Synthetic'))
        else:
            assert asyncio.run(embedding.embed('Synthetic')) == [1., 0.]
    assert len(calls) == len(receipts) == 1
    assert receipts[0]['status'] == ('failed' if fail else 'returned')
    assert receipts[0]['usage'] == (None if fail else {'total_tokens':3})
