"""Hard-capped public two-question selector diagnostic. Zero inference by default."""
from __future__ import annotations
import copy
import json
from urllib.parse import urlsplit

from ..memory.temporal_query import TemporalQueryParser
from ..memory.evidence import EvidenceBudget, requirements_for
from ..memory.evidence.requirements import coverage
from ..memory.evidence_selector import select_evidence
from ..observability.memory_trace import fingerprint, execution_failure
from ..observability.timing import StageTimings
from ..prompting.reader_budget import ReaderBudget
from .history import canonical
from .locomo import parse_answer
from .locomo_eval import prepare, EnglishLexicalIndex, SYSTEM, CaseRecorder, answer_f1
from .records import RunRecorder
from .roles import ExperimentLLM, RoleConfig, load_roles
from .runner import account_settings, local_policy


def pool_for(history,question,index):
    anchors=index.select(history,question,16)
    positions={row['id']:i for i,row in enumerate(history)}
    chosen=list(anchors);seen={r['id'] for r in chosen}
    for anchor in anchors[:4]:
        position=positions[anchor['id']]
        for at in (position-1,position+1):
            if 0<=at<len(history) and history[at]['id'] not in seen:
                chosen.append(history[at]);seen.add(history[at]['id'])
    parsed=TemporalQueryParser().parse(question);requirements=requirements_for(question,parsed)
    candidates=[]
    for rank,row in enumerate(chosen,1):
        c={'id':row['id'],'kind':'source','score':1/(60+rank),'source_ids':[row['id']],
            'data':{'text':row['text'],'speaker':row['speaker'],'session_date_raw':row['session_date_raw'],
                'evidence':{'message_id':row['id'],'start':0,'end':len(row['text']),
                            'content_sha256':row['source_text_sha256']}}}
        c['covers']=coverage(c,requirements,parsed);candidates.append(c)
    return candidates,requirements


async def run_micro(workspace,settings,source,*,with_reader=False,allow_remote=False,profile=None,client_factory=ExperimentLLM):
    bundle,protocol,_,_=prepare(workspace,source,'micro')
    cases=[json.loads(line) for line in (bundle/'inputs.jsonl').read_text(encoding='utf-8').splitlines()]
    histories=json.loads((bundle/'histories.json').read_text(encoding='utf-8'))
    if len(cases)!=2:raise ValueError('Micro diagnostic requires exactly two frozen source-order questions')
    roles=load_roles(None)
    if with_reader:
        settings=account_settings(copy.deepcopy(settings),workspace,profile)
        local_policy(settings,roles,allow_remote)
        selected=settings.main
        if not selected.is_ready(role='llm'):raise ValueError('Reader is not configured')
        thinking='disabled_by_deepseek_api' if urlsplit(selected.base_url).hostname=='api.deepseek.com' else 'provider_default_unknown'
        roles['reader']=RoleConfig(max_tokens=512,timeout_seconds=60,max_retries=0,thinking=thinking)
        settings.reader_input_bytes=12000
        if not (selected.is_local_http() or selected.is_local_process()):settings.reader_context_tokens=None
    recorder=RunRecorder(workspace.path('experiments'),settings.root,
        datasets=[bundle/'inputs.jsonl',bundle/'histories.json'],models={},seed=None,
        protocol={**protocol,'id':'locomo-budget-micro-v1','mode':'locomo_budget_micro',
            'frozen_inputs_protocol':protocol['id'],'owner_user_id':workspace.owner,
            'scope':'two source-order questions; development diagnostic, never full benchmark',
            'retrieval':'English BM25 16 anchors + at most 8 adjacent turns; no embeddings',
            'budget_unit':'utf8_bytes','budgets':[1000,2000,3000],'reader_evidence_budget':3000,
            'reader_methods':['topk','evidence_coverage'] if with_reader else [],
            'reader_call_limit':4,'reader_output_limit':512,'reader_input_byte_limit':12000,
            'reader_enabled':with_reader,'retries':0,'fallback':'none','automatic_memory_writes':0,
            'reader_system_sha256':fingerprint(SYSTEM),'gold_usage':'post-inference scoring only'})
    index=EnglishLexicalIndex();rows=[];calls=[];fatal=None
    metrics={'errors':0,'requested_cases':2,'max_reader_calls':4,'actual_reader_calls':0,
             'quality':'unscored' if not with_reader else 'tiny development diagnostic; not a quality estimate',
             'production_writes':0,'money_cost':None,'methods':{}}
    try:
        for case in cases:
            pool,requirements=pool_for(histories[case['conversation_id']],case['question'],index)
            selections={str(limit):{strategy:select_evidence(pool,requirements['facets'],limit,strategy,8,
                budget=EvidenceBudget(limit,'utf8_bytes')) for strategy in ('topk','coverage','mmr','evidence_coverage')}
                for limit in (1000,2000,3000)}
            recorder.append('events.jsonl',{'id':case['id'],'candidate_pool_sha256':fingerprint(pool),
                'candidate_pool':pool,'requirements':requirements,'selections':selections,'gold_read':False})
            if not with_reader:continue
            for method in ('topk','evidence_coverage'):
                if len(calls)>=4:raise RuntimeError('Micro reader call limit reached')
                identity=case['id']+':'+method
                client=client_factory(settings,roles,CaseRecorder(recorder,identity),StageTimings())
                recorder.manifest['models']={'reader':client.public_roles()['reader']};recorder._save_manifest()
                selection=selections['3000'][method]
                presented=[c['id'] for c in selection['payload']['evidence']]
                messages=[{'role':'system','content':SYSTEM},{'role':'user','content':canonical(
                    {'question':case['question'],'selection':selection['payload']})}]
                row={'id':case['id'],'method':method,'status':'error','answer':None,'presented_ids':presented,
                     'candidate_pool_sha256':fingerprint(pool),'selection_cost':selection['cost']}
                try:
                    phase='budget'
                    prepared=await ReaderBudget(settings).prepare(messages,selected=selected,max_tokens=512,temperature=0,top_p=1)
                    if prepared.messages!=messages:raise ValueError('Complete micro evidence/question was not presented')
                    row['reader_budget']=prepared.report
                    phase='call'
                    result=await client.call('reader',prepared.messages)
                    phase='parse'
                    row['answer']=parse_answer(result,presented);row['status']='success'
                except Exception as exc:
                    row.update(execution_failure(exc,phase))
                finally:
                    calls.extend({**c,'case_id':case['id'],'method':method} for c in client.calls)
                rows.append(row);recorder.append('cases.jsonl',row)
        # Retrieval, coverage and model prompts above never receive these labels.
        gold={g['id']:g for g in (json.loads(line) for line in (bundle/'gold.jsonl').read_text(encoding='utf-8').splitlines())}
        for method in ('topk','evidence_coverage') if with_reader else ():
            group=[r for r in rows if r['method']==method]
            scores=[answer_f1(r['answer']['answer'],gold[r['id']]['answer'],gold[r['id']]['category'])
                    if r['status']=='success' else 0 for r in group]
            metrics['methods'][method]={'requested':2,'completed':sum(r['status']=='success' for r in group),
                'errors':sum(r['status']!='success' for r in group),'mean_token_f1':sum(scores)/2,
                'answered':sum(r['status']=='success' and r['answer']['status']=='answer' for r in group),
                'semantic_support':'not independently verified'}
        known=[c['usage']['total_tokens'] for c in calls if isinstance(c.get('usage'),dict) and type(c['usage'].get('total_tokens')) is int]
        metrics.update(errors=sum(r['status']!='success' for r in rows),actual_reader_calls=len(calls),
            known_tokens_lower_bound=sum(known) if known else (0 if not calls else None),
            unknown_usage_calls=len(calls)-len(known),requested_reader_rows=4 if with_reader else 0)
    except BaseException as exc:
        fatal=type(exc).__name__;raise
    finally:
        known=[c['usage']['total_tokens'] for c in calls if isinstance(c.get('usage'),dict) and type(c['usage'].get('total_tokens')) is int]
        metrics.update(actual_reader_calls=len(calls),known_tokens_lower_bound=sum(known) if known else (0 if not calls else None),
                       unknown_usage_calls=len(calls)-len(known),uncompleted_reader_rows=(4-len(rows)) if with_reader else 0)
        index.close();recorder.finish(metrics,error=fatal)
    return recorder.path
