"""Public aggregates must exclude account paths, questions, gold, answers and prompts."""
import hashlib
import json
from pathlib import Path

import pytest

from scripts import publish_eval_summary as publication


def test_public_export_keeps_denominator_but_excludes_private_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(publication, 'ROOT', tmp_path)
    source = tmp_path/'data/benchmarks/locomo/raw/locomo10.json'
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps([{'sample_id': 'conversation', 'conversation': {'session_1': []},
        'qa': [{'question': 'PRIVATE_QUESTION', 'answer': 'PRIVATE_GOLD', 'category': 1},
               {'question': 'PRIVATE_ADVERSARIAL', 'category': 5}]}]), encoding='utf-8')
    run = tmp_path/'data/users/private-owner/experiments/current/run'
    run.mkdir(parents=True)
    manifest = {'run_id': 'run', 'status': 'running', 'started_at_utc': '2026-10-23T00:00:00Z',
        'code': {'commit': 'abc', 'dirty_diff_sha256': 'def'},
        'protocol': {'protocol': 'kernel-vs-context-llm-judge-v3-ordered-histories',
            'dataset_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'model': 'deepseek-flash',
            'judge_prompt_sha256': 'judge-hash'}}
    (run/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    item = publication.load_items('locomo', source)[0]
    row = publication.ArmResult(item.id, 'memoria', question='PRIVATE_QUESTION', ground_truth='PRIVATE_GOLD',
        group='1', answer='PRIVATE_ANSWER', score=1, correct=True,
        extras={'messages': 'PRIVATE_PROMPT', 'account_path': str(run)})
    (run/'cases.jsonl').write_text(json.dumps(row.to_dict())+'\n', encoding='utf-8')
    result = publication.aggregate(run)
    raw = json.dumps(result)
    assert all(value not in raw for value in ['PRIVATE_', 'private-owner', str(run), item.id])
    assert result['arms'][0]['items'] == 2 and result['arms'][0]['not_run'] == 1
    assert result['arms'][1]['items'] == 2 and result['arms'][1]['not_run'] == 2


def test_export_rejects_an_unscoped_run_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(publication, 'ROOT', tmp_path)
    with pytest.raises(ValueError, match='account-private'):
        publication.aggregate(tmp_path/'public')
