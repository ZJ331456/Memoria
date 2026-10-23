"""Export only aggregate metrics from a private two-arm run; never copy prompts or answers."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from memoria.experiments.eval_benchmarks import load_items
from memoria.experiments.eval_schema import ArmResult, summarize
from memoria.experiments.deepseek_cache import cost_bounds
from memoria.llm.deepseek_cache import cache_summary

CATEGORIES = {'4': ('Single-hop', 841), '1': ('Multi-hop', 282),
              '3': ('Open-domain', 96), '2': ('Temporal', 321), '5': ('Adversarial', 446)}


def lines(path):
    if not path.exists():
        return
    # The final unterminated line can be an active write; never turn it into a result.
    with path.open(encoding='utf-8') as handle:
        for line in handle:
            if line.endswith('\n') and line.strip():
                yield json.loads(line)


def aggregate(run):
    private = ROOT / 'data/users'
    run = run.resolve()
    if not run.is_relative_to(private.resolve()):
        raise ValueError('Expected an account-private run under data/users')
    manifest = json.loads((run/'manifest.json').read_text(encoding='utf-8'))
    protocol = manifest['protocol']
    if protocol.get('protocol') not in {'kernel-vs-context-llm-judge-v3-ordered-histories',
                                        'kernel-vs-context-llm-judge-v4-session-corpus-best-effort'}:
        raise ValueError('Only a registered sequential protocol may be published')
    source = ROOT/'data/benchmarks/locomo/raw/locomo10.json'
    if hashlib.sha256(source.read_bytes()).hexdigest() != protocol['dataset_sha256']:
        raise ValueError('Dataset fingerprint differs')
    items = load_items('locomo', source, conversations=10)
    # Native checkpoints can exceed a gigabyte: retain only scalar aggregation fields.
    fields = {'id', 'arm', 'group', 'correct', 'score', 'error', 'latency_ms', 'answer_status'}
    visibility = {'completed': 0, 'truncated_questions': 0, 'max_dropped_turns': 0}
    def keep(row):
        if row.get('arm') == 'raw_context' and 'dropped_turns' in row.get('extras', {}):
            dropped = row['extras']['dropped_turns']
            visibility['completed'] += 1
            visibility['truncated_questions'] += bool(dropped)
            visibility['max_dropped_turns'] = max(visibility['max_dropped_turns'], dropped)
        return {k: v for k, v in row.items() if k in fields}
    checkpoints = [keep(row) for row in lines(run/'cases.jsonl')]
    calls = list(lines(run/'calls.jsonl'))
    known = {(r['arm'], r['id']): r for r in checkpoints}
    if len(known) != len(checkpoints):
        raise ValueError('Duplicate checkpoint IDs must be investigated, not overwritten')
    arms = []
    for name in ['memoria', 'raw_context']:
        rows = []
        for item in items:
            row = known.get((name, item.id))
            rows.append(ArmResult(**row) if row else ArmResult(item.id, name, group=item.group, error='not_run'))
        summary = summarize(name, rows).to_dict()
        # Publish only the declared primary/category scores and authoritative ledger usage.
        # Scored-only averages and per-answer usage would misrepresent missing grades
        # and Kernel's multi-call lifecycle (which has no single answer usage object).
        for key in ('item_ids', 'avg_score', 'accuracy', 'accuracy_interpretation', 'tokens', 'cache'):
            summary.pop(key, None)
        summary['checkpointed'] = sum((name, item.id) in known for item in items)
        summary['not_run'] = sum(bool(r.error and r.error.startswith('not_run')) for r in rows)
        summary['execution_errors'] = dict(Counter(r.error for r in rows if r.error and not r.error.startswith('not_run')))
        summary['status'] = 'complete' if not summary['not_run'] else 'running' if manifest['status'] == 'running' else 'stopped'
        arm_calls = [r for r in calls if r.get('arm') == name]
        summary['calls'] = len(arm_calls)
        summary['usage'] = cache_summary(arm_calls)
        summary['cost'] = cost_bounds(summary['usage'], protocol['model'])
        summary['stages'] = []
        for stage in sorted({r.get('stage', 'unknown') for r in arm_calls}):
            subset = [r for r in arm_calls if r.get('stage', 'unknown') == stage]
            usage = cache_summary(subset)
            summary['stages'].append({'stage': stage, 'calls': len(subset), 'usage': usage,
                'cost': cost_bounds(usage, protocol['model']),
                'summed_call_ms': sum(r.get('elapsed_ms', 0) for r in subset)})
        arms.append(summary)
    formation, approvals, reasons, heads = Counter(), Counter(), Counter(), Counter()
    for db in (run/'kernel').rglob('*.db'):
        with sqlite3.connect(db.resolve().as_uri()+'?mode=ro', uri=True, timeout=5) as connection:
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('library_sources', 'library_observations', 'library_consolidations', 'claims', 'memories'):
                if table in tables:
                    formation[table] += connection.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
            if {'library_observations', 'library_cards'} <= tables:
                for status, count in connection.execute('SELECT c.status,COUNT(*) FROM library_observations o JOIN library_cards c ON c.id=o.card_id GROUP BY c.status'):
                    approvals[status] += count
                for (verdict,) in connection.execute('SELECT c.verdict_json FROM library_observations o JOIN library_cards c ON c.id=o.card_id'):
                    reasons.update(json.loads(verdict).get('admission_reasons', []))
            if {'library_consolidations', 'library_consolidation_versions'} <= tables:
                for status, count in connection.execute("SELECT COALESCE(v.status,'no_head'),COUNT(*) FROM library_consolidations r LEFT JOIN library_consolidation_versions v ON r.head_id=v.id GROUP BY v.status"):
                    heads[status] += count
    return {'schema': 'memoria-public-eval-2', 'run_id': manifest['run_id'],
        'status': manifest['status'], 'started_at_utc': manifest['started_at_utc'],
        'finished_at_utc': manifest.get('finished_at_utc'), 'elapsed_ms': manifest.get('elapsed_ms'),
        'protocol': protocol['protocol'], 'source_sha256': protocol['dataset_sha256'],
        'code_commit': manifest['code']['commit'], 'dirty_diff_sha256': manifest['code']['dirty_diff_sha256'],
        'model': protocol['model'], 'judge_prompt_sha256': protocol['judge_prompt_sha256'],
        'category_counts': {name: count for name, count in CATEGORIES.values()},
        'arms': arms, 'formation_table_counts': dict(formation),
        'baseline_visibility': visibility,
        'formation_admission': {'observation_statuses': dict(approvals),
            'admission_reasons_overlapping': dict(reasons), 'consolidation_head_statuses': dict(heads)},
        'limits': ['All questions used for development; not unseen generalization.',
                   'External-source native Kernel path; no implied personal Claim/View or Dense participation.',
                   'Failures and unscored cases retained in planned denominator; partial accuracy is a lower bound.',
                   'Latency is answer-stage; formation and judge calls/cost are separate.',
                   'Estimated frozen-price cost, not provider invoice. Private raw data are excluded.']}


def presentation(payload):
    """Publish a transparent table and a brand-colored chart from aggregates only."""
    import os
    os.environ.setdefault('MPLCONFIGDIR', str(ROOT/'.venv/.matplotlib-cache'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
    labels = {'memoria': 'Memoria 内核＋同一模型', 'raw_context': 'Baseline：完整／截断历史＋模型'}
    rows = []
    for arm in payload['arms']:
        values = []
        for key in ['4', '1', '3', '2', 'primary']:
            metric = arm['primary'] if key == 'primary' else arm['by_group'][key]
            values.append(f"{metric['accuracy']:.2%}" if arm['status'] == 'complete' else '待完成')
        rows.append('| '+labels[arm['arm']]+' | '+' | '.join(values)+f" | {arm['scored']:,}/1,986 | {arm['errors']-arm['not_run']} |")
    supplementary = []
    for arm in payload['arms']:
        g = arm['by_group']['5']
        value = f"{g['accuracy']:.2%}" if arm['status'] == 'complete' else '待完成'
        cost = arm['cost']['estimate']
        lo, hi = (cost[p]['known_input_output_usd'] for p in ['off_peak', 'peak'])
        latency = f"{arm['latency_ms']/1000:.2f}s" if arm['latency_ms'] is not None else '—'
        supplementary.append(f"| {labels[arm['arm']]} | {value} | {g['scored']}/446 | {arm['calls']:,} | ${lo:.3f}–{hi:.3f} | {latency} |")
    finished = all(arm['status'] == 'complete' for arm in payload['arms'])
    state = '全量计划题均已执行；执行／评分失败已保留' if finished else '评估进行中' if payload['status'] == 'running' else '已停止，存在未执行题'
    findings = ''
    if finished:
        memory, baseline = payload['arms']
        findings = f"\n**本轮诊断：** Memoria有{memory['errors']}题执行／评分失败，Baseline有{baseline['errors']}题；失败留在计划分母。"
        if memory['primary']['accuracy'] < baseline['primary']['accuracy']:
            findings += ' 当前完整分母主分未体现框架收益；下一步优先处理覆盖检查契约、来源日期表达、开放域回答策略及必要证据组预算，再以新协议复测。'
        findings += '\n'
    admission = payload.get('formation_admission', {})
    if admission:
        states = admission.get('observation_statuses', {})
        missing = admission.get('admission_reasons_overlapping', {}).get('subject_binding_missing', 0)
        published = admission.get('consolidation_head_statuses', {}).get('approved', 0)
        findings += f"\n形成诊断：Observation批准 **{states.get('approved', 0)}** 条、待辅助 **{states.get('needs_assistance', 0)}** 条；其中 **{missing}** 条包含主体绑定缺失原因，已发布巩固版本 **{published}**。来源窗口中的说话者元数据尚未充分形成唯一主体证据链，当前多数候选未进入有效记忆。下一步先修复绑定及来源时间/覆盖契约，再验证形成机制收益；不绕过审核或为提分伪造本人身份。\n"
    section = '''## LoCoMo 全量评测

首轮正式比较采用[顺序全量协议](eval/protocols/kernel-vs-context-judge-v3-ordered-histories.json)：**先Memoria完整一组，再Baseline**；同组最多8段独立历史并发，每段库独立、题目顺序执行。相同DeepSeek Flash、非思考、Reader输出上限1024；10段历史、5,882原话，每组1,986题。评分为适配Mem0规则的独立LLM二分类，不是Token F1。

| 方法 | 单跳（841） | 多跳（282） | 开放域（96） | 时序（321） | Overall（1,540） | 已判题数 | 执行错误 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
'''+ '\n'.join(rows)+'''

Overall按四类答对总数除以1,540，非四类均值。全部失败/未评分保留原分母；存在未评分时百分比是保守下界，不是已评分子集准确率。类别5不计入主分：

| 方法 | 对抗拒答二分类（446） | 对抗已判 | 全生命周期调用 | 全生命周期费用估算USD | 平均回答阶段延迟 |
| --- | ---: | ---: | ---: | ---: | ---: |
'''+ '\n'.join(supplementary)+f'''

运行状态：**{state}**；Run `{payload['run_id']}`。费用包含形成/审核/巩固、回答/支持/修复及独立评分，按冻结离峰–高峰单价估算，非账单；未知usage不补零。回答延迟含内核检索与支持，但不含形成摊销及Judge；完整阶段费用和耗时见[脱敏聚合](assets/evaluation/current.json)。串行前测已独立保留并计费，不混入本表成绩。
{findings}

![LoCoMo分类与完整分母](assets/evaluation/current.png)

两组使用同一骨干；公开对话人物保留外部身份，通过生产Kernel资料域读写，不冒充账号本人。个人Claim/View与个人Dense/RRF不因统一Kernel而自动参与。全部题已用于开发，本次属于同题开发复测；框架整体差异不能证明单一机制因果收益或真实长期陪伴效果。[评分、失败与成本边界](docs/implemented/双组内核评估与费用估算.md)

旧结果退出当前展示。原文、gold、逐题答案、提示词、失败、调用账本和隔离SQLite只保存在本机，不上传远程。

'''
    path = ROOT/'README.md'
    visibility = payload.get('baseline_visibility', {})
    if visibility.get('completed'):
        section = section.replace('Overall按四类答对总数',
            f"Baseline实际记录{visibility['completed']}题上下文，截断{visibility['truncated_questions']}题、最多丢弃{visibility['max_dropped_turns']}轮；本轮均保留完整历史。\n\nOverall按四类答对总数" if not visibility['truncated_questions'] else
            f"Baseline实际截断{visibility['truncated_questions']}题，最多丢弃{visibility['max_dropped_turns']}轮，均从最早整轮移除。\n\nOverall按四类答对总数")
    text = path.read_text(encoding='utf-8')
    start, end = text.index('## LoCoMo 全量评测'), text.index('## 仓库目录')
    path.write_text(text[:start]+section+text[end:], encoding='utf-8')
    plt.rcParams.update({'svg.fonttype': 'path', 'svg.hashsalt': 'memoria-eval-v3'})
    fig, ax = plt.subplots(figsize=(14, 6), facecolor='#0B122B')
    ax.set_facecolor('#0B122B')
    keys = ['4', '1', '3', '2', 'primary', '5']
    for i, arm in enumerate(payload['arms']):
        positions = [x+(-.19 if i == 0 else .19) for x in range(6)]
        values = [(arm['primary'] if key == 'primary' else arm['by_group'][key])['accuracy'] for key in keys]
        complete = arm['status'] == 'complete'
        bars = ax.bar(positions, values if complete else [0]*6, width=.35,
            color=['#38D5F5', '#A68BFF'][i], label=['Memoria + same model', 'Full-context baseline'][i])
        for bar, value in zip(bars, values):
            ax.text(bar.get_x()+bar.get_width()/2, (value if complete else 0)+.02,
                f'{value:.1%}' if complete else 'pending', ha='center', fontsize=10, color='#F6F8FF', rotation=0 if complete else 90)
    ax.set_ylim(0, 1.14)
    ax.yaxis.set_major_formatter(PercentFormatter(1))
    ax.set_xticks(range(6), ['Single-hop\n841', 'Multi-hop\n282', 'Open-domain\n96', 'Temporal\n321', 'Overall\n1,540', 'Adversarial\n446'])
    ax.tick_params(colors='#AFBEDD')
    for spine in ax.spines.values(): spine.set_color('#334978')
    ax.grid(axis='y', alpha=.15); ax.set_axisbelow(True)
    ax.set_title('LoCoMo / independent binary LLM judge / full planned denominator', color='#F6F8FF', fontsize=16, pad=20)
    legend = ax.legend(facecolor='#101C3B', edgecolor='#334978', loc='upper left')
    for t in legend.get_texts(): t.set_color('#F6F8FF')
    fig.text(.12, .02, 'Same backbone. Development retest. Unknown grades/errors remain in denominator; incomplete scores are lower bounds.', color='#AFBEDD', fontsize=9)
    fig.tight_layout(rect=(0, .06, 1, 1))
    for ext in ['png', 'svg']: fig.savefig(ROOT/f'assets/evaluation/current.{ext}', dpi=150, metadata={'Date': None, 'Creator': 'Memoria aggregate renderer'} if ext == 'svg' else {'Software': 'Memoria aggregate renderer'})
    svg = ROOT/'assets/evaluation/current.svg'
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines())+'\n', encoding='utf-8', newline='\n')
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'assets/evaluation/current.json')
    parser.add_argument('--update-readme', action='store_true')
    parser.add_argument('--preflight-run', help='Explicit earlier run ID in this same account; costs only, never quality')
    args = parser.parse_args()
    payload = aggregate(args.run)
    snapshot = ROOT/'assets/evaluation/source-code.manifest.json'
    if snapshot.is_file():
        source = json.loads(snapshot.read_text(encoding='utf-8'))
        if source.get('run_id') == payload['run_id']:
            payload['runtime_source_manifest'] = 'source-code.manifest.json'
            payload['runtime_source_tree_sha256'] = source['source_tree_sha256']
    protocol = json.loads((args.run/'manifest.json').read_text(encoding='utf-8'))['protocol']
    if previous := args.preflight_run or protocol.get('preflight_run'):
        if Path(previous).name != previous:
            raise ValueError('Invalid preflight run ID')
        directory = args.run.resolve().parents[1]/'kernel-vs-context-llm-judge-v2-serial'/previous
        if (directory/'calls.jsonl').is_file():
            previous_protocol = json.loads((directory/'manifest.json').read_text(encoding='utf-8'))['protocol']
            if previous_protocol['dataset_sha256'] != payload['source_sha256'] or previous_protocol['model'] != payload['model']:
                raise ValueError('Preflight input/model differ; do not silently reprice or merge')
            rows, events = list(lines(directory/'calls.jsonl')), list(lines(directory/'events.jsonl'))
            returned = {r['call'] for r in rows}
            unfinished = [r for r in events if r['call'] not in returned]
            usage = cache_summary(rows)
            payload['preflight_accounting'] = {'run_id': previous, 'quality_excluded': True,
                'returned_calls': len(rows), 'reserved_requests': len(events),
                'unfinished_reservations': len(unfinished),
                'unfinished_reserved_upper_usd': sum(r['reserved_upper_usd'] for r in unfinished),
                'usage': usage, 'cost': cost_bounds(usage, payload['model']),
                'note': 'Unknown in-flight usage remains unknown; not a zero-cost retry.'}
        else:
            payload['preflight_accounting'] = {'run_id': previous, 'status': 'unavailable', 'cost_unknown': True}
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    if args.update_readme:
        presentation(payload)
    print(json.dumps({'run': payload['run_id'], 'status': payload['status'],
                     'arms': [{k: a[k] for k in ['arm', 'status', 'checkpointed', 'scored', 'not_run', 'errors']} for a in payload['arms']]}))


if __name__ == '__main__':
    main()
