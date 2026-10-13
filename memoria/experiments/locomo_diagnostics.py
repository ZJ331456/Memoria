"""Offline triage and audited token attribution; gold is used only AFTER inference."""
from __future__ import annotations

import json
from pathlib import Path
from collections import Counter
from .history import identifier
from .records import digest_file
from ..memory.answer_risk import normalized, query_risk
from ..memory.reader import strict_json


def read_json(path):
    return strict_json(path.read_text(encoding='utf-8'))


def lines(path):
    with path.open(encoding='utf-8') as file:
        return [strict_json(line) for line in file if line.strip()]


def verified_run(workspace, run_id):
    path = workspace.path('experiments', identifier(run_id))
    manifest = read_json(path / 'manifest.json')
    if manifest['status'] == 'running' or manifest['protocol']['owner_user_id'] != workspace.owner:
        raise ValueError('Requires stopped same-owner run')
    for name, expected in manifest['artifacts'].items():
        if name != Path(name).name or digest_file(path / name) != expected['sha256']:
            raise ValueError('Recorded artifact changed')
    return path, manifest


def lineage(workspace, run_id, seen=None):
    seen = set() if seen is None else seen
    if run_id in seen or len(seen) >= 8:
        raise ValueError('Invalid run lineage')
    seen.add(run_id)
    path, manifest = verified_run(workspace, run_id)
    calls = []
    if manifest.get('resume'):
        parent_path, parent = verified_run(workspace, manifest['resume']['source_run'])
        for name, expected in manifest['resume']['source_artifacts'].items():
            if digest_file(parent_path / name) != expected['sha256']:
                raise ValueError('Ancestor differs from inherited snapshot')
        calls.extend(lineage(workspace, parent['run_id'], seen)[2])
    if (path / 'calls.jsonl').exists():
        calls.extend({**c, 'origin_run_id': run_id} for c in lines(path / 'calls.jsonl'))
    return path, manifest, calls


def token_cost(calls, denominator):
    known = [c['usage'] for c in calls if c.get('usage') is not None]
    totals = {k: sum(u.get(k, 0) or 0 for u in known) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    return {'cases': denominator, 'attempts': len(calls), 'known_usage_calls': len(known),
            'unknown_usage_calls': len(calls)-len(known), 'failed_calls': sum(bool(c.get('error')) for c in calls),
            'tokens_known': totals, 'mean_known_tokens_per_case': totals['total_tokens']/denominator if denominator else None,
            'scope': 'recorded lineage attempts incl engineering retries; missing/in-flight usage is not zero cost',
            'monetary_cost': None}


def gate_proxy(rows, details, histories):
    """Restricted offline labels, NOT semantic sufficiency ground truth."""
    confusion = Counter()
    skipped = 0
    for detail in details:
        row = rows[detail['id']]
        initial = next((d for d in row.get('sufficiency_checks',[]) if d['round']==0),None)
        if initial is None:
            skipped += 1
            continue
        if detail['category']==5:
            positive=False
        elif detail['category']==4:
            visible=set(row['primary_presented_ids'])
            source=histories[detail['id'].rsplit('-q',1)[0]]
            answer=normalized(detail['reference_answer']).strip().rstrip('.')
            if not answer or not any(m['id'] in visible and answer in normalized(m['text']) for m in source):
                skipped += 1
                continue
            positive=True
        else:
            skipped += 1
            continue
        if initial.get('reason')=='assessor_contract_rejected':
            confusion['unknown_positive' if positive else 'unknown_negative']+=1
        else:
            predicted=initial['state']=='sufficient'
            confusion['tp' if positive and predicted else 'fn' if positive else 'fp' if predicted else 'tn']+=1
    tp,fn,fp,tn=(confusion[k] for k in ('tp','fn','fp','tn'))
    up,un=confusion['unknown_positive'],confusion['unknown_negative']
    positive_f1=2*tp/(2*tp+fp+fn+up) if 2*tp+fp+fn+up else None
    negative_f1=2*tn/(2*tn+fp+fn+un) if 2*tn+fp+fn+un else None
    return {'labels':'official category5 negatives; category4 answer literal in primary text positives',
            'counts':dict(confusion),'evaluated':tp+fn+fp+tn+up+un,'excluded':skipped,
            'unknown_decisions':up+un,'analysis_version':'gate-proxy-v2-unknown-contract-errors-not-credited-as-negatives',
            'macro_f1':(positive_f1+negative_f1)/2 if positive_f1 is not None and negative_f1 is not None else None,
            'positive_recall':tp/(tp+fn+up) if tp+fn+up else None,'negative_recall':tn/(tn+fp+un) if tn+fp+un else None,
            'independent_semantic_gold':False,'model_confidence_used':False}


def compare(workspace, baseline_run, run_ids):
    baseline_path, baseline_manifest, baseline_calls=lineage(workspace,baseline_run)
    baseline_details=read_json(baseline_path/'score.json')['details']
    methods=[]
    chosen_ids=None
    for run_id in run_ids:
        path,manifest,calls=lineage(workspace,run_id)
        metrics=read_json(path/'metrics.json')
        details=read_json(path/'score.json')['details']
        ids={d['id'] for d in details}
        if chosen_ids is not None and ids!=chosen_ids:
            raise ValueError('Comparison case sets differ')
        chosen_ids=ids
        if manifest['protocol']['source_sha256']!=baseline_manifest['protocol']['source_sha256']:
            raise ValueError('Benchmark source differs')
        if manifest['models']['reader']!=baseline_manifest['models']['reader']:
            raise ValueError('Reader settings differ; not a matched comparison')
        if manifest['model_snapshots']!=baseline_manifest['model_snapshots']:
            raise ValueError('Embedding snapshot differs')
        methods.append({'method':manifest['protocol']['fallback'],'run_id':run_id,
                        'code':manifest['code'],'protocol':{k:v for k,v in manifest['protocol'].items()
                            if k not in {'owner_user_id','case_ids'}},
                        'metrics':metrics,'cost':token_cost(calls,len(ids)),'artifacts':manifest['artifacts']})
        if manifest['protocol']['fallback']=='adaptive_risk':
            rows={r['id']:r for r in lines(path/'cases.jsonl')}
            bundle=workspace.path('research','benchmarks','locomo-'+manifest['protocol']['source_sha256'][:12],
                                  'locomo-text-english-rrf-fallback-v5-'+manifest['protocol']['scope'])
            if digest_file(bundle/'histories.json')!=manifest['protocol']['histories_sha256']:
                raise ValueError('Frozen history changed')
            methods[-1]['gate_proxy']=gate_proxy(rows,details,read_json(bundle/'histories.json'))
    if not chosen_ids or not chosen_ids<={d['id'] for d in baseline_details}:
        raise ValueError('Comparison cases outside original benchmark')
    selected=[d for d in baseline_details if d['id'] in chosen_ids]
    ordinary=[d for d in selected if d['category']<5]
    adversarial=[d for d in selected if d['category']==5]
    calls=[c for c in baseline_calls if c['case_id'] in chosen_ids]
    baseline=[]
    for primary in (True,False):
        field='primary_answer_f1' if primary else 'answer_f1'
        baseline.append({'method':'original_rrf_primary' if primary else 'original_full_history',
            'run_id':baseline_run,'code':baseline_manifest['code'],
            'ordinary_f1':sum(d[field] for d in ordinary)/len(ordinary) if ordinary else None,
            'adversarial_refusal':sum(d[field] for d in adversarial)/len(adversarial) if adversarial else None,
            'cost':token_cost([c for c in calls if c['call'].endswith(':0')] if primary else calls,len(selected)),
            'inflight_usage_unknown':baseline_manifest.get('resume',{}).get('source_inflight_calls_unknown',0)})
    report={'cases':len(selected),'ordinary_cases':len(ordinary),'adversarial_cases':len(adversarial),
            'baseline':baseline,'new_methods':methods,'same_original_case_ids':True,
            'same_reader_and_embedding_snapshots':True,'independent_human_review':False,
            'semantic_accuracy':None,'unseen_test_claim':False,'production_memory_writes':0}
    target=workspace.path('reports','locomo-risk-comparison-'+run_ids[-1])
    target.mkdir(exist_ok=False)
    (target/'aggregate.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return target,report


def flags_for(row, detail, source_texts):
    question = row.get('question', '')
    flags = list(query_risk(question)['flags'])
    if detail['unresolved_evidence_ids']:
        flags.append('unresolved_gold_id')
    if detail['presented_gold_recall'] is None:
        flags.append('gold_evidence_not_defined')
    elif detail['presented_gold_recall'] < 1:
        flags.append('gold_ids_not_all_presented')
    else:
        flags.append('gold_ids_all_presented')
    cited = [source_texts.get(mid, '') for mid in row.get('evidence_ids', [])]
    answer = normalized(row.get('answer', '')).strip().rstrip('.')
    if answer and any(answer in normalized(t) for t in cited):
        flags.append('answer_literal_in_cited_text')
    reference = normalized(detail['reference_answer']).strip()
    if reference and any(reference in normalized(t) for t in source_texts.values()):
        flags.append('reference_literal_in_source_text')
    else:
        flags.append('reference_literal_not_found_in_text')
    if detail['category'] == 3 and ';' in reference:
        alternatives = [p.strip() for p in reference.split(';')[1:]]
        if any(a and (a in answer or answer in a) for a in alternatives):
            flags.append('unscored_reference_alternative_literal_match')
    return sorted(set(flags))


def triage_class(flags, abstained):
    if {'unresolved_gold_id', 'gold_evidence_not_defined'} & set(flags):
        return 'annotation_or_undefined_evidence_review'
    if 'gold_ids_not_all_presented' in flags:
        return 'retrieval_or_presentation_gap_review'
    if 'unscored_reference_alternative_literal_match' in flags:
        return 'reference_alternative_review'
    if abstained and 'reference_literal_in_source_text' in flags:
        return 'reader_or_subject_condition_review'
    if 'temporal_relation' in flags or 'absolute_or_first' in flags:
        return 'temporal_precision_or_premise_review'
    if 'inference_requested' in flags or 'medical_or_high_impact' in flags:
        return 'background_inference_or_high_impact_review'
    return 'semantic_span_or_answer_wording_review'


def audit(workspace, run_id):
    path, manifest, calls = lineage(workspace, run_id)
    metrics, scores = read_json(path/'metrics.json'), read_json(path/'score.json')
    rows = {r['id']: r for r in lines(path/'cases.jsonl')}
    if len(rows) != metrics['requested_cases'] or set(rows) != {d['id'] for d in scores['details']}:
        raise ValueError('Audit denominator mismatch')
    source_path = workspace.path('research','benchmarks','locomo-'+manifest['protocol']['source_sha256'][:12],
                                 'locomo-text-english-rrf-fallback-v5-'+manifest['protocol']['scope'])
    if digest_file(source_path/'histories.json') != manifest['protocol']['histories_sha256']:
        raise ValueError('Frozen source changed')
    histories = read_json(source_path/'histories.json')
    private = []
    for detail in scores['details']:
        if detail['category'] == 5 or detail['execution_status'] != 'success':
            continue
        abstained = detail['answer_status'] != 'answer'
        if not abstained and detail['answer_f1'] != 0:
            continue
        row = rows[detail['id']]
        history = histories[detail['id'].rsplit('-q',1)[0]]
        source_texts = {m['id']:m['text'] for m in history}
        flags = flags_for(row, detail, source_texts)
        private.append({'id': detail['id'], 'group': 'abstained' if abstained else 'answered_zero_f1',
                        'question': row['question'], 'reference': detail['reference_answer'],
                        'answer': row['answer'], 'category': detail['category'], 'flags': flags,
                        'triage': triage_class(flags,abstained), 'semantic_correct': None,
                        'notes': 'Rule-based review queue, not a semantic verdict',
                        'cited_sources': [{'id':mid,'text':source_texts.get(mid)} for mid in row['evidence_ids']]})
    summary = {'source_run': run_id, 'requested_cases': len(rows),
               'source_artifacts': manifest['artifacts'], 'heuristic_triage_only': True, 'independent_human_review': False,
               'groups': {group: {'cases':len([r for r in private if r['group']==group]),
                   'triage':dict(Counter(r['triage'] for r in private if r['group']==group)),
                   'flags':dict(Counter(f for r in private if r['group']==group for f in r['flags']))}
                   for group in ('abstained','answered_zero_f1')},
               'primary_token_cost': token_cost([c for c in calls if c['call'].endswith(':0')],len(rows)),
               'full_history_token_cost': token_cost(calls,len(rows)),
               'inflight_calls_unknown': manifest.get('resume',{}).get('source_inflight_calls_unknown',0),
               'model_calls':0,'scores_rewritten':False}
    target = workspace.path('reports','locomo-triage-'+run_id)
    target.mkdir(exist_ok=False)
    (target/'private-review.json').write_text(json.dumps(private,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (target/'aggregate.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return target, summary
