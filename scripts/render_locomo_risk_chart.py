"""Render audited aggregate trade-offs only; no inference, private histories or new scores."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def data_rows(report):
    if not report['same_original_case_ids'] or not report['same_reader_and_embedding_snapshots']:
        raise ValueError('Unmatched comparison')
    rows=[]
    for b in report['baseline']:
        rows.append({'method':b['method'],'f1':b['ordinary_f1'],'refusal':b['adversarial_refusal'],
                     'tokens':b['cost']['mean_known_tokens_per_case']})
    for method in report['new_methods']:
        m=method['metrics']
        rows.append({'method':method['method'],'f1':m['ordinary_answer_f1'],
                     'refusal':m['by_category']['5']['official_refusal_phrase_rate'],
                     'tokens':method['cost']['mean_known_tokens_per_case']})
    expected=['original_rrf_primary','original_full_history','targeted','adaptive_risk']
    if (len(rows)!=4 or {r['method'] for r in rows}!=set(expected)
            or any(not 0<=r['f1']<=1 or not 0<=r['refusal']<=1 or r['tokens']<0 for r in rows)):
        raise ValueError('Invalid aggregate scores')
    lookup={r['method']:r for r in rows}
    return [lookup[method] for method in expected]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--aggregate',type=Path,required=True,help='Explicit archived aggregate; old scores are not current results')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--check',action='store_true',help='Verify existing chart and source fingerprints without rendering')
    args=parser.parse_args()
    report=json.loads(args.aggregate.read_text(encoding='utf-8'))
    rows=data_rows(report)
    if args.check:
        path=args.output.with_name(args.output.name+'-chart.json')
        saved=json.loads(path.read_text(encoding='utf-8'))
        sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        assert saved['source_sha256']==sha(args.aggregate) and saved['rows']==rows
        assert saved['model_calls']==0 and saved['new_scores']==0
        assert set(saved['images'])=={args.output.name+'.png',args.output.name+'.svg'}
        assert all(sha(args.output.parent/name)==value for name,value in saved['images'].items())
        print('Verified full comparison source, rows and chart fingerprints; no inference or scoring.')
        return
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    matplotlib.rcParams.update({'svg.hashsalt':'memoria-locomo-risk-v2','font.family':'DejaVu Sans'})
    labels=['RRF primary','Full history','Targeted','Risk adaptive']
    fig,axes=plt.subplots(1,2,figsize=(13,5.3),layout='constrained')
    x=np.arange(4);width=.35
    for values,offset,color,title in (
        ([r['f1']*100 for r in rows],-width/2,'#2474b5','Ordinary Answer F1'),
        ([r['refusal']*100 for r in rows],width/2,'#8b5bb0','Adversarial refusal phrase score')):
        bars=axes[0].bar(x+offset,values,width,color=color,label=title)
        axes[0].bar_label(bars,fmt='%.2f',padding=3,fontsize=8)
    axes[0].set_xticks(x,labels,rotation=15)
    axes[0].set_ylim(0,112);axes[0].set_ylabel('Score (%)')
    axes[0].set_title('Quality and refusal are separate metrics')
    axes[0].legend(loc='upper left',fontsize=8)
    values=[r['tokens'] for r in rows]
    bars=axes[1].bar(x,values,color=['#6f899c','#aebcc6','#4c9d81','#d0874b'])
    axes[1].bar_label(bars,fmt='%.0f',padding=3,fontsize=9)
    axes[1].set_xticks(x,labels,rotation=15)
    axes[1].set_ylim(0,max(values)*1.2)
    axes[1].set_ylabel('Known tokens / question (lower bound)')
    axes[1].set_title('All Reader, planner and verifier calls included')
    for ax in axes:
        ax.spines[['top','right']].set_visible(False)
        ax.set_axisbelow(True);ax.grid(axis='y',alpha=.15)
    fig.suptitle(f"Memoria / LoCoMo text-only / {report['cases']} original QA",fontsize=14,fontweight='bold')
    fig.text(.5,-.015,'DeepSeek flash + local English BGE; historical retries retained. No semantic/human accuracy claim.',
             ha='center',fontsize=9)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    png=args.output.with_suffix('.png');svg=args.output.with_suffix('.svg')
    fig.savefig(png,dpi=160,bbox_inches='tight')
    fig.savefig(svg,bbox_inches='tight',metadata={'Date':None})
    svg.write_bytes(('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines())+'\n').encode('utf-8'))
    plt.close(fig)
    sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    fingerprint={'source':args.aggregate.name,'source_sha256':sha(args.aggregate),'cases':report['cases'],'rows':rows,
                 'images':{p.name:sha(p) for p in (png,svg)},'matplotlib_version':matplotlib.__version__,
                 'model_calls':0,'new_scores':0}
    args.output.with_name(args.output.name+'-chart.json').write_bytes((json.dumps(fingerprint,indent=2)+'\n').encode('utf-8'))
    print('Rendered aggregate chart; model calls=0; new scores=0.')


if __name__=='__main__':
    main()
