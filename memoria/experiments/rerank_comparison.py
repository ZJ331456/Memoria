"""Four predeclared LoCoMo questions, external source Reader, paired frozen candidates."""
import asyncio,copy,html,json,time,uuid
from pathlib import Path
from ..store import Store
from ..llm import LLMClient
from ..memory.kernel import MemoryKernel
from ..memory.library import SourceInput
from ..memory.records import KnowledgeAnswer
from .history import write_json
from .records import digest_file
from .simulation import core_fingerprint
from .locomo import SOURCE_SHA256
from .locomo_eval import answer_f1

METHODS=('source_rank','llm_rerank')

def inputs(path):
    if digest_file(path)!=SOURCE_SHA256:raise ValueError('Official source checksum mismatch')
    records=json.loads(Path(path).read_text(encoding='utf-8'));record=records[0]
    questions=[{'id':record['sample_id']+'-q'+str(i),'index':i,'question':record['qa'][i]['question']} for i in range(4)]
    history=[];conversation=record['conversation']
    for session in sorted((k for k in conversation if k.startswith('session_') and k[8:].isdigit()),key=lambda k:int(k[8:])):
        for turn in conversation[session]:
            history.append({'dia_id':turn['dia_id'],'speaker':turn['speaker'],'text':turn['text'],
                'session_date_raw':conversation[session+'_date_time']})
    return {'questions':questions,'history':history,'source_sha256':SOURCE_SHA256,
        'selection':'first conversation, QA indexes 0,1,2,3; no model result/answer/evidence selection'}

async def run(settings,path,*,max_calls=12,budget_chars=3600):
    if max_calls!=12 or not 1200<=budget_chars<=8000:raise ValueError('Protocol fixes four questions and twelve-call ceiling')
    frozen=inputs(path);core=core_fingerprint(settings.root)
    directory=settings.root/'simulation/runs'/('rerank-comparison-'+uuid.uuid4().hex[:12]);directory.mkdir(parents=True)
    write_json(directory/'inputs.snapshot.json',frozen)
    manifest={'status':'running','protocol':'production-library-fixed-pool-rerank-v1','core_sha256':core,
        'data_kind':'public_constructed_benchmark_subset','questions':4,'max_calls':12,'candidate_limit':4,
        'evidence_budget_chars':budget_chars,'model':settings.main.model,'reader_output_tokens':1400,
        'scope':'external documents/direct admission; library lexical pool, not personal RRF/Claim/formation/long-term effect'}
    write_json(directory/'manifest.json',manifest)
    settings=copy.deepcopy(settings);settings.database=directory/'isolated.db';settings.markdown_enabled=False
    settings.max_retries=0;settings.max_tokens=1400
    store=Store(settings.database,'json');client=LLMClient(settings);original=client.chat;calls=[];rows=[];source_ids={};context={}
    async def bounded(messages,**kwargs):
        if len(calls)>=12:raise RuntimeError('Predeclared twelve-call ceiling exhausted')
        row={**context,'messages':messages,'status':'attempted','generation':{k:v for k,v in kwargs.items() if k!='model'}}
        calls.append(row);started=time.perf_counter()
        try:
            result=await original(messages,**kwargs)
            row.update(status='returned',content=result.content,usage=result.usage or None,finish_reason=result.finish_reason);return result
        except Exception as exc:row['error']=type(exc).__name__;raise
        finally:row['elapsed_ms']=round((time.perf_counter()-started)*1000,3);write_json(directory/'calls.json',calls)
    client.chat=bounded;kernel=MemoryKernel(settings,store,client,user_id='benchmark:rerank')
    try:
        for item in frozen['history']:
            body=f"Speaker: {item['speaker']}\nOriginal session date (not event date): {item['session_date_raw']}\nUtterance: {item['text']}"
            saved=kernel.ingest(SourceInput(kind='document',title=item['dia_id']+' '+item['speaker'],content=body,authorship='external'))
            source_ids[saved['id']]=item['dia_id']
        write_json(directory/'source-map.json',{k:{'domain':'library','id':k,'original_dia_id':v} for k,v in source_ids.items()})
        original_search=kernel.search
        for index,question in enumerate(frozen['questions']):
            request=KnowledgeAnswer(query=question['question'],limit=4,budget_chars=budget_chars,use_entities=False)
            capture={};started=time.perf_counter();packet=await original_search(request,capture=capture)
            retrieval_ms=round((time.perf_counter()-started)*1000,3)
            write_json(directory/(question['id']+'-pool.json'),{'packet':packet,'capture':capture,'retrieval_ms':retrieval_ms})
            async def frozen_search(_,*,capture=None):
                if capture is not None:capture.update(copy.deepcopy(frozen_capture))
                return copy.deepcopy(packet)
            frozen_capture=copy.deepcopy(capture);kernel.search=frozen_search
            ordered=METHODS if index%2==0 else tuple(reversed(METHODS))
            for method in ordered:
                context.update(case=question['id'],method=method)
                started=time.perf_counter();before=len(calls)
                row={'case':question['id'],'method':method,'shared_retrieval_ms':retrieval_ms,
                    'candidate_original_ids':[source_ids[e['source_id']] for e in capture['candidates']]}
                try:
                    result=await kernel.answer(request.model_copy(update={'use_llm_rerank':method=='llm_rerank'}))
                    row.update(result=result,status='finished')
                except Exception as exc:row.update(status='failed',error=type(exc).__name__)
                row.update(elapsed_ms=round((time.perf_counter()-started)*1000,3),call_indices=list(range(before,len(calls))))
                rows.append(row);write_json(directory/'predictions.json',rows)
            kernel.search=original_search
        # Open labels only after all inference. They never enter query/rank/Reader inputs.
        official=json.loads(Path(path).read_text(encoding='utf-8'))[0]['qa'];gold=[]
        for question in frozen['questions']:
            q=official[question['index']];gold.append({'id':question['id'],'answer':str(q['answer']),'category':q['category'],'evidence':q['evidence']})
        write_json(directory/'gold.snapshot.json',gold);lookup={g['id']:g for g in gold}
        for row in rows:
            result=row.get('result',{});g=lookup[row['case']];answer=result.get('answer','') if result.get('status')=='answer' and result.get('validation',{}).get('valid') else ''
            row['f1']=answer_f1(answer,g['answer'],g['category'])
            row['selected_original_ids']=[source_ids[e['source_id']] for e in result.get('access',{}).get('evidence',[])]
            expected=set(g['evidence']);row['visible_gold_recall']=len(expected & set(row['selected_original_ids']))/len(expected) if expected else None
        summary={}
        for method in METHODS:
            group=[r for r in rows if r['method']==method];mc=[c for c in calls if c['method']==method]
            summary[method]={'questions':4,'mean_token_f1':sum(r['f1'] for r in group)/4,
                'answers':sum(r.get('result',{}).get('status')=='answer' for r in group),'calls':len(mc),
                'known_tokens':sum((c.get('usage') or {}).get('total_tokens',0) for c in mc),'unknown_usage_calls':sum(not c.get('usage') for c in mc),
                'mean_pipeline_ms':sum(r['elapsed_ms']+r['shared_retrieval_ms'] for r in group)/4,
                'rerank_applied':sum(r.get('result',{}).get('access',{}).get('reranking',{}).get('status')=='applied' for r in group)}
        result={'protocol':manifest['protocol'],'summary':summary,'rows':rows,'core_sha256':core,
            'core_unchanged':core_fingerprint(settings.root)==core,'production_writes':0,
            'execution_failures':sum(r['status']=='failed' for r in rows),'quality':'four-question lexical-F1 diagnostic, not representative/full LoCoMo or independently verified semantics'}
        write_json(directory/'results.json',result)
        content='<meta charset="utf-8"><title>LLM重排配对诊断</title><style>body{font:16px sans-serif;margin:32px}td,th{border:1px solid #ccc;padding:12px}pre{white-space:pre-wrap}</style><h1>四题LoCoMo来源Reader配对诊断</h1><p>固定来源候选/预算、交替调用顺序。非全量，非个人RRF/Claim/长期效果。失败保留分母。</p><table><tr><th>方法</th><th>题数</th><th>Token F1</th><th>调用</th><th>已知Tokens</th><th>平均总延迟ms</th></tr>'
        for method,s in summary.items():content+='<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in [method,4,round(s['mean_token_f1'],4),s['calls'],s['known_tokens'],round(s['mean_pipeline_ms'],1)])+'</tr>'
        content+='</table><pre>'+html.escape(json.dumps(result,ensure_ascii=False,indent=2))+'</pre>'
        (directory/'report.html').write_text(content,encoding='utf-8')
        store.close()  # Freeze SQLite before checksumming artifacts; no transient WAL/SHM entries.
        manifest.update(status='finished',artifacts={p.name:digest_file(p) for p in directory.iterdir() if p.is_file() and p.name!='manifest.json'})
        write_json(directory/'manifest.json',manifest)
        return {'directory':str(directory),'summary':summary,'core_sha256':core,'core_unchanged':result['core_unchanged'],'execution_failures':result['execution_failures'],'production_writes':0}
    finally:store.close()
