"""Render the public LoCoMo aggregate only; no private data, model calls or rescoring."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]


def main():
    import argparse
    argparse.ArgumentParser(description=__doc__).parse_args()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    source = ROOT/'docs/assets/evaluation/locomo-full-20261013.json'
    report = json.loads(source.read_text(encoding='utf-8'))
    metrics = report['metrics']
    assert metrics['ordinary_cases'] == 1540 and metrics['adversarial_cases'] == 446
    assert metrics['errors'] == 0 and metrics['requested_cases'] == 1986
    plt.rcParams.update({'svg.hashsalt':'memoria-locomo-20261013','font.family':'DejaVu Sans'})
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), gridspec_kw={'width_ratios':[2.3,1]})
    groups = metrics['by_category']
    labels = ['Multi-hop\nn=282','Temporal\nn=321','Open-domain\nn=96','Single-hop\nn=841']
    colors = ['#2879b9','#e59037']
    for axis, categories in [(axes[0],['1','2','3','4']), (axes[1],['5'])]:
        first = [groups[c]['primary_score']*100 for c in categories]
        final = [groups[c].get('answer_f1',groups[c].get('official_refusal_phrase_rate'))*100 for c in categories]
        for delta, values, color, title in [(-.18, first, colors[0], 'Initial RRF reader'),(.18, final, colors[1], '+ complete-history recheck')]:
            bars=axis.bar([i+delta for i in range(len(categories))],values,.34,color=color,label=title)
            axis.bar_label(bars,labels=[f'{v:.2f}' for v in values],padding=3,fontsize=9)
        axis.set_ylim(0,105);axis.set_yticks(range(0,101,20));axis.grid(axis='y',alpha=.18);axis.set_axisbelow(True)
        axis.spines[['top','right']].set_visible(False)
    axes[0].set_xticks(range(4),labels);axes[0].set_ylabel('Answer F1 (%)');axes[0].set_title('Ordinary QA: 1,540 questions')
    axes[1].set_xticks([0],['Adversarial\nn=446']);axes[1].set_title('Official refusal phrase score (%)')
    axes[0].legend(loc='upper left',fontsize=8)
    fig.suptitle('LoCoMo-10 text-only: DeepSeek reader + local English BGE',fontsize=13)
    fig.text(.5,.015,'F1 and refusal phrase score are not semantic accuracy. All 1,986 cases retained; engineering retries recorded.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.065,1,.94))
    base=source.with_suffix('')
    outputs=[]
    for suffix in ('png','svg'):
        target=base.with_suffix('.'+suffix)
        fig.savefig(target,dpi=180,metadata={'Date':None} if suffix=='svg' else {})
        if suffix=='svg':
            target.write_bytes(('\n'.join(line.rstrip() for line in target.read_text(encoding='utf-8').splitlines())+'\n').encode('utf-8'))
        outputs.append({'file':target.name,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
    plt.close(fig)
    manifest={'source':source.name,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
              'renderer':'scripts/render_locomo_chart.py','matplotlib':matplotlib.__version__,
              'model_calls':0,'new_scores':0,'outputs':outputs}
    base.with_name(base.name+'-chart.json').write_bytes((json.dumps(manifest,indent=2)+'\n').encode('utf-8'))
    print(json.dumps({'chart_written':True,'private_data_read':False,'model_calls':0}))


if __name__=='__main__':
    main()
