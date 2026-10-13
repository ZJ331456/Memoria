"""Temporary engineering fixtures, not benchmark results or personal history."""
import asyncio
import json

import pytest

from memoria.config import Settings
from memoria.experiments import locomo_eval as bench
from memoria.experiments.history import HistoryWorkspace
from memoria.experiments.roles import load_roles
from memoria.llm import ChatResult


def fixture_records():
    return [{'sample_id': f'conv-{i}', 'conversation': {
        'speaker_a': 'Temporary A', 'speaker_b': 'Temporary B', 'session_1_date_time': 'original date text',
        'session_1': [{'dia_id': 'D1:1', 'speaker': 'Temporary A', 'text': 'Original temporary source.'}]},
        'qa': [{'category': c, 'question': 'Temporary question?', 'answer': 'GOLD_SENTINEL',
                'evidence': ['D1:1']} for c in (1, 2, 3, 4)]} for i in range(10)]


def turns():
    return [dict(id='c/D1:'+str(i), dia_id='D1:'+str(i), content=text, content_sha256=bench.sha(text), text=text,
                 speaker='Temporary speaker', session_id=session, session_date_raw='source date')
            for i, (text, session) in enumerate([('I adopted three dogs in 2020.', 's1'),
                 ('They are happy here.', 's1'), ('This other session is unrelated.', 's2')], 1)]


def test_english_spaces_stemming_and_cached_index():
    history = turns()
    index = bench.EnglishLexicalIndex()
    try:
        assert index.select(history, 'Which year did she adopt dogs?', 10)[0]['id'] == 'c/D1:1'
        assert index.select(history, 'NoEvidenceMatchingToken', 10) == []
        assert index.select(history, 'How was it?', 10) == []
        assert len(index.indexes) == 1
        assert history[0]['text'] == 'I adopted three dogs in 2020.'
    finally:
        index.close()


def test_adjacent_context_same_session_dedup_and_real_projection_budget():
    history = turns()
    result = bench.select_context(history, [history[1], history[0]], question='dogs', anchor_limit=1)
    assert result['presented_ids'] == ['c/D1:1', 'c/D1:2']
    assert history[2]['id'] not in result['presented_ids']
    assert len(result['presented_ids']) == len(set(result['presented_ids']))
    assert result['cost_chars'] == len(bench.canonical(result['payload']))
    assert all(set(e) == {'id', 'speaker', 'session_date_raw', 'text'} for e in result['payload']['evidence'])
    small = bench.select_context(history, [history[0]], question='dogs', budget_chars=40)
    assert small['presented_ids'] == [] and small['trace'][0]['decision'] == 'budget'
    modified = {**history[0], 'text': 'tampered'}
    with pytest.raises(ValueError, match='frozen'):
        bench.select_context(history, [modified], question='dogs')


def test_late_ranked_anchor_is_kept_before_early_neighbors():
    history=turns()
    # Budget fits both anchors but not all three source turns.
    exact=len(bench.canonical({'evidence':[{k: m[k] for k in ('id','speaker','session_date_raw','text')}
                                            for m in (history[0], history[2])]}))
    result=bench.select_context(history,[history[0],history[2]],question='temporary',budget_chars=exact)
    assert result['presented_ids']==[history[0]['id'],history[2]['id']]


def test_annotation_syntax_diagnostics_never_replace_primary_labels():
    histories={'c':turns()}
    gold=[{'id':'c-q0','unresolved_evidence_ids':['c/D1:1 D1:2','c/D:1:1','c/D1:01','c/D','c/D1:999']}]
    result=bench.audit_gold(gold,histories)
    assert result['syntax_resolvable_entries']==3 and result['unrecoverable_entries']==2
    assert gold[0]['unresolved_evidence_ids'][-2:] == ['c/D','c/D1:999']


def test_full_includes_adversarial_without_leaking_or_requiring_answer():
    records = fixture_records()
    for r in records:
        r['qa'].append({'question': 'Unanswerable temporary question?', 'category': 5,
                        'adversarial_answer': 'GOLD_ADVERSARIAL', 'evidence': ['missing']})
    cases, histories, gold = bench.benchmark_cases(records, 'full')
    assert len(cases) == len(gold) == 50 and len(histories) == 10
    assert 'GOLD' not in json.dumps([cases, histories])
    assert len([g for g in gold if g['category'] == 5]) == 10
    assert all(g['answer'] == 'Not mentioned.' for g in gold if g['category'] == 5)
    assert bench.benchmark_cases(records, 'smoke')[0] == [c for c in cases if c['id'] in
           {c['id'] for c in bench.smoke_cases(records)[0]}]


@pytest.mark.parametrize('prediction,reference,category,expected', [
    ('snake', 'snakes', 4, 1), ('middle class', 'Middle-class; wealthy', 3, 0),
    ('dogs', 'dogs; also cats', 3, 1), ('a championship', 'shooting percentage, championship', 1, .5),
    ('Not mentioned.', 'irrelevant', 5, 1), ('Cannot establish this.', 'irrelevant', 5, 0)])
def test_upstream_category_specific_metric(prediction, reference, category, expected):
    assert bench.answer_f1(prediction, reference, category) == expected


def test_failures_and_missing_labels_are_not_dropped_or_repaired():
    cases, _, gold = bench.benchmark_cases(fixture_records(), 'full')
    gold[0]['evidence_ids'].append('conv-0/D')
    gold[0]['unresolved_evidence_ids'].append('conv-0/D')
    rows = [{'id': c['id'], 'status': 'error'} for c in cases]
    result = bench.score(rows, gold)
    assert result['requested_cases'] == 40 and result['ordinary_answer_f1'] == 0
    assert result['unresolved_gold_entries'] == 1
    assert result['by_category']['1']['cases'] == 10
    with pytest.raises(ValueError, match='denominator'):
        bench.score(rows[:-1], gold)


def test_bundle_no_gold_input_immutable(tmp_path, monkeypatch):
    source = tmp_path/'source.json'
    source.write_text(json.dumps(fixture_records()), encoding='utf-8')
    monkeypatch.setattr(bench, 'SOURCE_SHA256', bench.digest_file(source))
    workspace = HistoryWorkspace(tmp_path/'owner')
    bundle, protocol, cases, histories = bench.prepare(workspace, source, 'full')
    assert protocol['requested_cases'] == 40
    assert 'GOLD' not in (bundle/'inputs.jsonl').read_text()
    assert 'GOLD' not in (bundle/'histories.json').read_text()
    (bundle/'gold.jsonl').write_text('modified')
    with pytest.raises(ValueError, match='changed'):
        bench.prepare(workspace, source, 'full')


@pytest.mark.parametrize('first_output', ['abstain', 'invalid', 'secondary_invalid'])
def test_concurrent_reader_call_ids_and_fallback(tmp_path, monkeypatch, first_output):
    source = tmp_path/'source.json'
    source.write_text(json.dumps(fixture_records()), encoding='utf-8')
    monkeypatch.setattr(bench, 'SOURCE_SHA256', bench.digest_file(source))
    class Embedding:
        namespace='temporary-test-only'
    class Retriever:
        document_encodes=10
        query_encodes=0
        def __init__(self, *a, **k): pass
        async def select(self, history, *a, **k):
            self.query_encodes += 1
            return history, {}
    monkeypatch.setattr(bench, 'PrefixRetriever', Retriever)
    class Reader:
        def __init__(self, settings, roles, recorder, timings): self.recorder=recorder; self.calls=[]
        def public_roles(self): return {'reader': {'model':'temporary'}}
        async def call(self, role, messages, **kwargs):
            packet=json.loads(messages[1]['content'])
            assert 'GOLD' not in json.dumps(messages)
            assert all(k not in packet for k in ('category','answer','evidence_ids'))
            call_id=len(self.calls)
            self.recorder.append('prompts.jsonl', {'call': call_id, 'messages': messages})
            await asyncio.sleep(.001)
            self.calls.append({'usage': {'total_tokens': 5}})
            self.recorder.append('calls.jsonl', {'call': call_id, 'usage': {'total_tokens': 5}})
            if self.recorder.case_id.endswith('-q0') and len(self.calls)==1:
                if first_output == 'invalid':
                    return ChatResult(content=json.dumps({'status':'answer','answer':'temporary','evidence_ids':['foreign']}))
                return ChatResult(content=json.dumps({'status':'abstain','answer':'Not mentioned.','evidence_ids':[]}))
            if self.recorder.case_id.endswith('-q0') and first_output=='secondary_invalid' and len(self.calls)==2:
                return ChatResult(content=json.dumps({'status':'answer','answer':'temporary','evidence_ids':[]}))
            return ChatResult(content=json.dumps({'status':'answer', 'answer':'temporary',
                               'evidence_ids':[packet['evidence'][0]['id']]}))
    config=tmp_path/'config.toml'
    config.write_text('[llm.main]\nmodel="temporary"\nbase_url="http://localhost:11434/v1"\n', encoding='utf-8')
    settings=Settings.load(config)
    workspace=HistoryWorkspace(tmp_path/'owner')
    path=asyncio.run(bench.execute(workspace,settings,source,scope='full',concurrency=4,
                 client_factory=Reader, embedding_factory=lambda cfg:Embedding(), roles=load_roles(None)))
    calls=[json.loads(line) for line in (path/'calls.jsonl').read_text().splitlines()]
    metrics=json.loads((path/'metrics.json').read_text())
    expected=60 if first_output=='secondary_invalid' else 50
    assert len(calls) == len({c['call'] for c in calls}) == expected and metrics['requested_cases'] == 40
    assert metrics['reader_calls'] == expected and metrics['fallback_cases'] == 10
    assert metrics['contract_repair_cases'] == (10 if first_output=='secondary_invalid' else 0)
    assert metrics['primary_validation_errors'] == (10 if first_output == 'invalid' else 0)
    assert metrics['fallback_errors'] == 0
    assert metrics['errors'] == 0 and metrics['automatic_memory_writes'] == 0
