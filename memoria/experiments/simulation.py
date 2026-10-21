"""Explicitly synthetic functional acceptance; never a companion-quality benchmark."""
from __future__ import annotations

import json
import html
import copy
import sqlite3
import time
import uuid
from pathlib import Path

from ..llm import LLMClient
from .history import canonical, write_json
from .production_replay import ClosedLoopInput, CallCeiling, execute_closed_loop
from .records import code_identity, digest_file


def core_fingerprint(root):
    """Freeze executable core/tests/fixtures, allowing later result documentation."""
    import hashlib
    files=sorted({*root.glob('memoria/**/*.py'), *root.glob('tests/**/*.py'),
        *root.glob('scripts/*.py'), *root.glob('simulation/*.json'),
        *root.glob('tests/fixtures/*.json'), *root.glob('eval/protocols/*.json'),
        *root.glob('eval/protocols/*.toml')})
    return hashlib.sha256(canonical({p.relative_to(root).as_posix():digest_file(p) for p in files}).encode()).hexdigest()


def acceptance_passed(result):
    return (result.get('acceptance_scope')=='complete_current_suite'
        and result.get('core_unchanged') is True and result.get('questions',0)>0
        and result.get('question_outcomes')=={'passed':result['questions']}
        and result.get('case_execution_errors')==0 and result.get('blocked_calls')==0)


def load_suite(root):
    path = root / 'simulation/scenarios.json'
    suite = json.loads(path.read_text(encoding='utf-8'))
    if suite.get('data_kind') != 'synthetic_functional' or suite.get('schema_version') != 1:
        raise ValueError('Only explicitly synthetic functional scenarios are accepted')
    seen = set()
    for case in suite['cases']:
        if case['id'] in seen or not case['id'].replace('_', '').isalnum():
            raise ValueError('Invalid or duplicate synthetic case ID')
        seen.add(case['id'])
        document = ClosedLoopInput.model_validate(case['input'])
        if not document.provenance.startswith('[SYNTHETIC]') or (document.subject_key and not document.subject_key.startswith('synthetic:')):
            raise ValueError('A synthetic subject must never be the real account owner')
    return suite


class TotalBudget:
    def __init__(self, max_calls, max_input_chars):
        self.max_calls, self.max_input_chars = max_calls, max_input_chars
        self.calls = self.input_chars = self.blocked = 0

    def client(self, settings):
        client = LLMClient(settings)
        original = client.chat

        async def bounded(messages, **kwargs):
            chars = len(json.dumps(messages,ensure_ascii=False,allow_nan=False))
            if self.calls >= self.max_calls or self.input_chars + chars > self.max_input_chars:
                self.blocked += 1
                raise CallCeiling('Synthetic suite total call/input budget exhausted')
            self.calls += 1
            self.input_chars += chars
            return await original(messages, **kwargs)

        client.chat = bounded
        return client


def observations_at(case, database):
    """Post-hoc facts from frozen prefix metadata + immutable revisions; no labels into models."""
    result = case.get('result', {})
    prefix = case.get('prefix', {})
    published = {}
    observations = reviews = cards = verified_history_links = unresolved_history = 0
    semantics_rows=[]
    for source in prefix.values():
        lineage = source['lineage']
        observations += len(lineage.get('observations', []))
        for observation in lineage.get('observations', []):
            if observation.get('semantics'):semantics_rows.append(observation['semantics'])
            association=(observation.get('history') or {}).get('association',{}).get('status')
            verified_history_links+=association=='verified'
            unresolved_history+=association=='unresolved'
        cards += sum(o.get('status') == 'approved' for o in lineage.get('observations', []))
        reviews += sum(r.get('status') == 'pending' for r in lineage.get('reviews', []))
        if lineage['available']:
            for p in lineage.get('published', []):
                if p.get('source_available', True):
                    published[(p['kind'], p['id'])] = p
    claims = [p for p in published.values() if p['kind'] == 'claim' and p.get('current') and p['status'] == 'approved']
    values = []
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        for claim in claims:
            row = db.execute("SELECT r.predicate,json_extract(v.data_json,'$.value') AS value FROM claim_revisions v JOIN claim_roots r ON r.id=v.claim_id WHERE v.id=?", (claim['id'],)).fetchone()
            if row:
                values.append(dict(row))
    trace = result.get('closed_loop', {})
    return {'execution': case['status'], 'status': result.get('status'),
        'path': result.get('access', {}).get('path'), 'answer': result.get('answer', ''),
        'reader_called': result.get('reader_called'), 'validation': result.get('validation', {}),
        'reranking_applied': result.get('access',{}).get('reranking',{}).get('status')=='applied',
        'adaptive_rounds': result.get('access',{}).get('adaptive',{}).get('retrieval_rounds',0),
        'adaptive_checks': len(result.get('access',{}).get('adaptive',{}).get('rounds',[])),
        'adaptive_complete': result.get('access',{}).get('selection',{}).get('complete',False),
        'adaptive_new_candidates': (len({c.get('id',c.get('citation')) for c in result.get('access',{}).get('candidates',[])} -
            {c.get('id',c.get('citation')) for c in result.get('access',{}).get('adaptive',{}).get('initial_candidates',[])})
            if result.get('access',{}).get('adaptive',{}).get('enabled') else 0),
        'active_claims': len(claims), 'claim_values': values,
        'active_memories': sum(p['kind'] == 'memory' and p['status'] == 'active' for p in published.values()),
        'active_patterns': sum(p['kind'] == 'pattern' and p['status'] == 'active' for p in published.values()),
        'active_source_consolidations': sum(p['kind']=='consolidation' and p.get('current') and p.get('root_status')=='active'
            and p.get('status')=='approved' for p in published.values()),
        'observations': observations, 'pending_reviews': reviews, 'approved_source_observations': cards,
        'verified_history_links':verified_history_links,'unresolved_history':unresolved_history,
        'dated_observations':sum(o['event_time'] is not None for o in semantics_rows),
        'unknown_time_observations':sum(o['event_time'] is None for o in semantics_rows),
        'planned_observations':sum(o['event_status']=='planned' for o in semantics_rows),
        'distinct_time_observations':sum(bool(o['event_time'] and o['source_time'] and
            o['event_time']['lower']!=o['source_time'] and o['source_time']!=o['source_recorded_at']) for o in semantics_rows),
        'citations': len(result.get('citations', [])), 'selected': len(trace.get('selected', [])),
        'pending_revision': bool(result.get('access', {}).get('pending_revisions')),
        'source_available': {k: v['lineage']['available'] for k, v in prefix.items()},
        'failure': case.get('failure')}


def check_observation(actual, expected):
    """Small closed assertion vocabulary; no eval, regex judge or LLM self-grading."""
    failures = []
    for key, value in expected.items():
        if key in {'status_in', 'path_in'}:
            ok = actual[key[:-3]] in value
        elif key.startswith('min_'):
            ok = actual[key[4:]] >= value
        elif key.startswith('max_'):
            ok = actual[key[4:]] <= value
        elif key in {'reader_called', 'pending_revision','reranking_applied','adaptive_complete'}:
            ok = actual[key] is value
        elif key == 'answer_any':
            ok = any(word.casefold() in actual['answer'].casefold() for word in value)
        elif key == 'answer_all':
            ok = all(word.casefold() in actual['answer'].casefold() for word in value)
        elif key == 'claim_predicate':
            ok = any(row['predicate'] == value for row in actual['claim_values'])
        elif key == 'source_available':
            ok = all(actual[key].get(k) is v for k, v in value.items())
        else:
            raise ValueError('Unknown synthetic assertion: ' + key)
        if not ok:
            failures.append({'check': key, 'expected': value})
    return failures


def inspect_case(path, labels):
    rows = [json.loads(line) for line in (path / 'cases.jsonl').read_text(encoding='utf-8').splitlines()]
    if set(labels) != {r['id'] for r in rows}:
        raise ValueError('Every question, including failures, requires a synthetic expectation')
    checks = []
    for row in rows:
        actual = observations_at(row, path / 'isolated-memory.db')
        expectation = labels[row['id']]
        safety = check_observation(actual, expectation.get('safety', {}))
        capability = check_observation(actual, expectation.get('capability', {}))
        outcome = ('execution_error' if actual['execution'] != 'completed' else 'safety_failure' if safety
                   else 'capability_gap' if capability else 'passed')
        checks.append({'question_id': row['id'], 'outcome': outcome, 'safety_failures': safety,
            'capability_failures': capability, 'actual': actual})
    return checks


def render_report(directory):
    directory=Path(directory)
    results=json.loads((directory/'results.json').read_text(encoding='utf-8'))
    rows=[]
    for case in results['cases']:
        for check in case.get('checks',[]):
            actual=check['actual']
            detail=html.escape(json.dumps(check,ensure_ascii=False,indent=2))
            rows.append('<tr><td>'+html.escape(case['id']+' / '+check['question_id'])+'</td><td>'+html.escape(case['title'])
                +'</td><td>'+html.escape(check['outcome'])+'</td><td>'+html.escape(str(actual.get('status')))
                +'</td><td><details><summary>断言与实际结果</summary><pre>'+detail+'</pre></details></td></tr>')
    summary=html.escape(json.dumps(results['summary'],ensure_ascii=False,indent=2))
    document='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Memoria 模拟功能验收</title>
<style>body{font:15px/1.65 system-ui;margin:32px;color:#243047;background:#f6f8fc}table{border-collapse:collapse;width:100%;background:white}
td,th{padding:12px;text-align:left;border:1px solid #dfe5ee;vertical-align:top}pre{white-space:pre-wrap;max-width:850px}h1{font-size:26px}</style>
<h1>Memoria 模拟功能验收</h1><p>模拟人物与数据；真实模型调用和工程检查分开记录。不是LoCoMo分数或真实长期效果。</p>
<p>passed=本次断言通过；capability_gap=能力未达预期；safety_failure=安全约束失败；execution_error=执行失败。未运行不算通过。</p>
<pre>'''+summary+'</pre><table><tr><th>场景/检查</th><th>目的</th><th>结果</th><th>实际返回</th><th>详情</th></tr>'+''.join(rows)+'</table></html>'
    (directory/'report.html').write_text(document,encoding='utf-8')
    return directory/'report.html'


async def offline_retrieval(workspace, settings):
    """Real offline BGE + production engine, with explicit direct fixture admission."""
    from ..models_config import apply_account_settings
    from ..memory import MemoryEngine, EmbeddingClient
    from ..memory.experience import local_encoder
    from ..store import Store
    configured=copy.deepcopy(settings);apply_account_settings(configured,workspace.directory)
    embedder=EmbeddingClient(configured.embedding,timeout_seconds=30,max_retries=0)
    if not local_encoder(embedder):raise ValueError('Offline diagnostic requires configured local BGE')
    directory=settings.root/'simulation/runs'/('offline-bge-'+uuid.uuid4().hex[:12]);directory.mkdir(parents=True)
    store=Store(directory/'isolated.db','json');engine=MemoryEngine(store,embedder,None)
    texts=['模拟用户的日常运动是骑自行车，常用于户外锻炼。','模拟用户的工作领域是后端数据库开发。',
        '\n'.join(f'模拟运行记录第{i}项：检查日志、请求队列、磁盘状态与服务心跳。' for i in range(120))
        +'\n演练项目的故障恢复流程使用快照回滚，然后检查索引一致性。']
    try:
        ids=[]
        for text in texts:
            source=store.add_message(store.create_session()['id'],'user',text)
            saved=await engine.remember(text,'fact',3,'synthetic_retrieval_fixture',source['id'],require_source=True)
            ids.append(saved.memory['id'])
        rows=[]
        for mode in ('keyword','bm25','dense','rrf'):
            diagnostic={}
            found=await engine.retrieve('故障恢复的快照回滚和索引一致性检查',mode=mode,diagnostics=diagnostic,expand_graph=False)
            rows.append({'mode':mode,'target_recalled':ids[-1] in [r['id'] for r in found],
                'returned':len(found),'diagnostics':diagnostic})
        receipt={'data_kind':'synthetic_functional','scope':'direct engine fixture admission, not LLM automatic approval',
            'code':code_identity(settings.root),'embedding_model':Path(configured.embedding.model).name,
            'model_calls':0,'production_writes':0,'modes':rows,'core_sha256':core_fingerprint(settings.root),
            'passed':all(r['target_recalled'] and (r['mode'] not in {'dense','rrf'} or
                (r['diagnostics'].get('embedding_attempted') is True and r['diagnostics'].get('dense_available') is True)) for r in rows)}
        write_json(directory/'receipt.json',receipt)
        return {'directory':str(directory),**receipt}
    finally:store.close()


def summarize_runs(root):
    """Derived cross-version status, retaining every original failure and run identity."""
    suite=load_suite(root)
    labels=json.loads((root/'simulation/expectations.json').read_text(encoding='utf-8'))
    current={c['id']:c for c in suite['cases']};latest={};history=[]
    paths=[p for p in (root/'simulation/runs').glob('*/results.json') if (p.parent/'manifest.json').is_file()]
    paths.sort(key=lambda p:json.loads((p.parent/'manifest.json').read_text(encoding='utf-8')).get(
        'started_at_ns',(p.parent/'manifest.json').stat().st_mtime_ns))
    for path in paths:
        directory=path.parent
        if not (directory/'manifest.json').is_file():continue
        manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('status')!='finished' or manifest.get('data_kind')!='synthetic_functional':continue
        if any(digest_file(directory/name)!=sha for name,sha in manifest['artifacts'].items()):
            raise ValueError('Frozen suite artifact changed')
        results=json.loads(path.read_text(encoding='utf-8'))
        history.append({'directory':directory.name,**results['summary']})
        for item in results['cases']:
            if item['id'] not in current:continue
            input_path=directory/item['run']/'input.json' if 'run' in item else directory/'inputs'/(item['id']+'.json')
            actual_input=json.loads(input_path.read_text(encoding='utf-8'))
            # Never substitute a successful different fixture or a different person.
            from .production_replay import ClosedLoopInput
            if canonical(ClosedLoopInput.model_validate(actual_input).model_dump(mode='json'))!=canonical(
                ClosedLoopInput.model_validate(current[item['id']]['input']).model_dump(mode='json')):continue
            latest[item['id']]={**item,'originating_suite':directory.name,
                'checks':inspect_case(directory/item['run'],labels['cases'][item['id']]) if 'run' in item else item['checks'],
                'code':manifest['code']}
    from collections import Counter
    outcomes=[q['outcome'] for c in latest.values() for q in c['checks']]
    summary={'data_kind':'synthetic_functional','scope':'cross-version cumulative functional checklist, not one final-version rerun or benchmark accuracy',
        'cases':len(latest),'missing_cases':sorted(set(current)-set(latest)),
        'question_outcomes':dict(Counter(outcomes)),
        'total_model_calls':sum(h['model_calls'] for h in history),
        'known_tokens':sum(h['known_tokens'] for h in history),'unknown_usage_calls':sum(h['unknown_usage_calls'] for h in history),
        'history':history,'production_writes':0}
    directory=root/'simulation/runs'/('status-'+uuid.uuid4().hex[:12]);directory.mkdir()
    write_json(directory/'results.json',{'summary':summary,'cases':list(latest.values())})
    render_report(directory)
    return {'directory':str(directory),**summary}


async def run_suite(workspace, settings, *, execute=False, allow_remote=False, max_calls=144,
                    max_input_chars=800000, only=None, progress=print, network_profile='environment'):
    root = settings.root
    suite = load_suite(root)
    cases = [c for c in suite['cases'] if only is None or c['id'] in only]
    if not cases or (only and set(only) != {c['id'] for c in cases}):
        raise ValueError('Unknown or empty synthetic selection')
    if type(max_calls) is not int or not 1 <= max_calls <= 144:
        raise ValueError('Use a total ceiling of 1..144 calls')
    if type(max_input_chars) is not int or not 1000 <= max_input_chars <= 1000000:
        raise ValueError('Invalid total character ceiling')
    if not execute:
        return {'status': 'validated_not_executed', 'cases': len(cases),
            'questions': sum(sum(e['kind'] == 'question' for e in c['input']['events']) for c in cases),
            'model_calls': 0, 'data_kind': 'synthetic_functional'}
    if not allow_remote:
        raise ValueError('Synthetic real-model execution requires --allow-remote')
    started = time.monotonic()
    directory = root / 'simulation/runs' / (time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    (directory / 'inputs').mkdir()
    budget = TotalBudget(max_calls, max_input_chars)
    expected_bytes = (root / 'simulation/expectations.json').read_bytes()
    (directory / 'expectations.snapshot.json').write_bytes(expected_bytes)
    write_json(directory / 'suite.snapshot.json', suite)
    manifest = {'data_kind': 'synthetic_functional', 'quality': 'functional_only_not_research',
        'core_sha256':core_fingerprint(root),
        'started_at_ns':time.time_ns(),
        'code': code_identity(root), 'source_sha256': digest_file(root / 'simulation/scenarios.json'),
        'expectations_sha256': digest_file(directory / 'expectations.snapshot.json'),
        'max_calls': max_calls, 'max_input_chars': max_input_chars, 'production_writes': 0,'network_profile':network_profile,
        'case_ids': [c['id'] for c in cases], 'status': 'running'}
    write_json(directory / 'manifest.json', manifest)
    executed = []
    for case in cases:
        source = directory / 'inputs' / (case['id'] + '.json')
        write_json(source, case['input'])
        try:
            path = await execute_closed_loop(workspace, settings, source, execute=True, allow_remote=True,
                max_calls=case.get('max_calls', 8), max_input_chars=case.get('max_input_chars', 60000),
                synthetic=True, output_root=directory / case['id'], client_factory=budget.client)
            metrics = json.loads((path / 'metrics.json').read_text(encoding='utf-8'))
            executed.append({'id': case['id'], 'title': case['title'], 'areas': case['areas'],
                'run': str(path.relative_to(directory)), 'metrics': metrics})
            progress(f"{case['id']}: {metrics['model_calls']} calls; suite {budget.calls}/{max_calls}")
        except Exception as exc:
            executed.append({'id': case['id'], 'title': case['title'], 'areas': case['areas'], 'error': type(exc).__name__})
            progress(f"{case['id']}: execution error {type(exc).__name__}")
        write_json(directory / 'execution.json', executed)
    # Expectations are first interpreted after every prediction has been frozen.
    labels = json.loads(expected_bytes)
    for item in executed:
        if 'run' in item:
            item['checks'] = inspect_case(directory / item['run'], labels['cases'][item['id']])
        else:
            item['checks']=[{'question_id':qid,'outcome':'execution_error','safety_failures':[],
                'capability_failures':[],'actual':{'execution':'error','status':None,'failure':{'type':item['error']}}}
                for qid in labels['cases'][item['id']]]
    outcomes = [q['outcome'] for c in executed for q in c.get('checks', [])]
    from collections import Counter
    totals = {'cases': len(cases), 'questions': len(outcomes), 'question_outcomes': dict(Counter(outcomes)),
        'model_calls': budget.calls, 'blocked_calls': budget.blocked,
        'known_tokens': sum(c.get('metrics', {}).get('known_total_tokens_lower_bound', 0) for c in executed),
        'unknown_usage_calls': sum(c.get('metrics', {}).get('unknown_usage_calls', 0) for c in executed),
        'case_execution_errors': sum('error' in c for c in executed),
        'elapsed_seconds': round(time.monotonic() - started, 3), 'production_writes': 0,
        'data_kind': 'synthetic_functional', 'quality': 'functional_assertions_not_semantic_accuracy'}
    totals.update(acceptance_scope='complete_current_suite' if only is None else 'partial_diagnostic',
        core_sha256=manifest['core_sha256'],core_unchanged=manifest['core_sha256']==core_fingerprint(root))
    totals['acceptance_passed']=acceptance_passed(totals)
    write_json(directory / 'results.json', {'summary': totals, 'cases': executed})
    render_report(directory)
    manifest.update(status='finished', artifacts={p.name: digest_file(p) for p in directory.iterdir() if p.is_file() and p.name != 'manifest.json'})
    write_json(directory / 'manifest.json', manifest)
    return {'directory': str(directory), **totals}
