"""Paired synthetic retrieval diagnostics, never a real-world quality benchmark.

Fixture admission is direct and explicit. The production access/selector/Reader
is reused; labels are opened only after all predictions have been saved.
"""
from __future__ import annotations

import copy
import html
import json
import time
import uuid
from pathlib import Path

from ..claims import sha
from ..memory.access import QueryRequest
from ..memory.entities import Anchor, EntityInput, AliasInput
from ..memory.kernel import MemoryKernel
from ..store import Store
from .history import write_json
from .records import digest_file, code_identity
from .simulation import core_fingerprint

METHODS=('three_lane_rrf','entity_filter','entity_rrf')


def load_inputs(root):
    value=json.loads((root/'simulation/entity_inputs.json').read_text(encoding='utf-8'))
    if value.get('data_kind')!='synthetic_functional' or value.get('revision')!=1:
        raise ValueError('Expected the declared synthetic protocol')
    cases=value['cases']
    if not 1<=len(cases)<=16 or len({c['id'] for c in cases})!=len(cases):raise ValueError('Invalid case IDs')
    for c in cases:
        if not c['id'].replace('_','').isalnum():raise ValueError('Invalid case path')
        QueryRequest(query=c['query'],entity_type=c.get('entity_type'))
        if not 1<=len(c['memories'])<=8:raise ValueError('Bounded corpus required')
        if len({m['key'] for m in c['memories']})!=len(c['memories']):raise ValueError('Duplicate fixture memory key')
        if any(not m['text'].strip() or len(m['text'])>12000 for m in c['memories']):raise ValueError('Invalid source size')
    return value


class MethodEngine:
    """Controls one retrieval policy without replacing production evidence selection."""
    def __init__(self,engine,catalog,method):
        if method not in METHODS:raise ValueError('Unknown method')
        self.engine,self.catalog,self.method=engine,catalog,method
        self.entity_enabled=method!='three_lane_rrf'

    def __getattr__(self,name):return getattr(self.engine,name)

    async def retrieve(self,query,**kwargs):
        kwargs.update(use_entities=self.method=='entity_rrf',expand_graph=False)
        rows=await self.engine.retrieve(query,**kwargs)
        if self.method!='entity_filter' or kwargs.get('as_of'):return rows
        session,kind=kwargs.get('session_id'),kwargs.get('entity_type')
        lookup=self.catalog.lookup(query,session_id=session,entity_type=kind)
        if lookup['resolution']['ambiguous']:return []
        ids=set(lookup['resolution']['entity_ids'])
        if not ids:return rows  # Unknown name retains the same open retrieval baseline.
        filtered=[]
        for row in rows:
            matched=sorted(ids & set(self.catalog.signature('memory',row['id'],sha(row['content']),session_id=session,entity_type=kind)))
            if matched:
                row=copy.deepcopy(row)
                row['retrieval']['entity_dependency']=self.catalog.dependency(lookup,'memory',row['id'],sha(row['content']),matched)
                filtered.append(row)
        if kwargs.get('diagnostics') is not None:kwargs['diagnostics']['entity_filter_returned']=len(filtered)
        return filtered


async def seed(kernel,case):
    """Direct fixture admission: deliberately does not evaluate extraction or approval."""
    sid=kernel.store.create_session()['id'];sources={};aliases={};memory_ids={}
    def message(key,text):
        row=kernel.record_message(sid,'user',text);sources[key]=row['id'];return row
    def anchor(row):return Anchor(domain='conversation',source_id=row['id'],start=0,end=len(row['content']),quote=row['content'],sha256=sha(row['content']))
    for e in case.get('entities',[]):
        row=message('entity:'+e['key'],e['name'])
        entity=kernel.entities.create(EntityInput(canonical_name=e['name'],entity_type=e['type'],evidence=anchor(row)))
        if e.get('alias'):
            row=message('alias:'+e['key'],e['name']+'又名'+e['alias']+'。')
            kernel.entities.alias(entity['id'],AliasInput(expected_revision=1,name=e['alias'],evidence=anchor(row)))
            aliases[e['key']]=row['id']
    texts=[m['text'] for m in case['memories']]
    vectors=await kernel.embedder.embed_documents(texts)
    if len(vectors)!=len(texts):raise ValueError('Incomplete local encoding')
    for fixture,encoded in zip(case['memories'],vectors,strict=True):
        row=message('memory:'+fixture['key'],fixture['text'])
        item=kernel.store.add_memory(fixture['text'],source='synthetic_entity_fixture',source_ref=row['id'],require_source=True,
            embedding=encoded.vector,embedding_namespace=kernel.embedder.namespace,embedding_chunks=encoded.chunks)
        kernel.entities.index_memory(item);memory_ids[fixture['key']]=item['id']
    for key in case.get('withdraw_sources',[]):kernel.store.undo_memory_sources([sources['memory:'+key]])
    for key in case.get('withdraw_aliases',[]):kernel.store.undo_memory_sources([aliases[key]])
    return memory_ids,sources


def score(row,labels,memory_ids):
    """Post-hoc ID diagnostics, not semantic entailment, QA F1 or human labels."""
    required={memory_ids[k] for k in labels.get('required',[])}
    forbidden={memory_ids[k] for k in labels.get('wrong_entity',[])}
    revoked={memory_ids[k] for k in labels.get('revoked',[])}
    access=row.get('access',{});candidates={c['id'] for c in access.get('candidates',[])}
    selected={c['id'] for c in access.get('selection',{}).get('payload',{}).get('evidence',[])}
    lane=set(access.get('retrieval',{}).get('lanes',{}).get('entity',[]))
    invalid_lane={memory_ids[k] for k in labels.get('invalid_entity_lane',[])}
    return {'required_count':len(required),'candidate_required':len(required & candidates),'selected_required':len(required & selected),
        'lost_after_retrieval':sorted(required & candidates-selected),'wrong_entity_selected':sorted(forbidden & selected),
        'revoked_exposed':sorted(revoked & (candidates|selected)),'invalid_entity_votes':sorted(lane & invalid_lane),
        'clarification_expected':labels.get('clarify',False),'clarification_observed':access.get('path')=='clarify',
        'budget_ok':bool(access) and access['selection']['cost']<=access['selection']['budget']['limit']}


async def run_comparison(settings,*,reader_cases=(),allow_remote=False,max_calls=0,budget_chars=8000):
    from ..llm import LLMClient
    from ..memory.experience import local_encoder
    from ..memory.embedding import EmbeddingClient
    suite=load_inputs(settings.root)
    if not set(reader_cases)<={c['id'] for c in suite['cases']}:raise ValueError('Unknown Reader case')
    if reader_cases and (not allow_remote or not 1<=max_calls<=12):raise ValueError('Reader requires explicit remote permission and call ceiling')
    if not 256<=budget_chars<=16000:raise ValueError('Invalid evidence budget')
    if not local_encoder(EmbeddingClient(settings.embedding)):raise ValueError('Comparison requires actual local BGE; no remote embedding')
    settings=copy.deepcopy(settings);settings.max_retries=0;settings.markdown_enabled=False
    directory=settings.root/'simulation/runs'/('entity-comparison-'+uuid.uuid4().hex[:12]);directory.mkdir(parents=True)
    write_json(directory/'inputs.snapshot.json',suite)
    # Freeze labels without interpreting them in any retrieval, selection or Reader input.
    (directory/'expectations.snapshot.json').write_bytes((settings.root/'simulation/entity_expectations.json').read_bytes())
    fingerprint=core_fingerprint(settings.root)
    manifest={'status':'running','data_kind':'synthetic_functional','core_sha256':fingerprint,'code':code_identity(settings.root),
        'protocol':'paired-entity-v1','methods':METHODS,'evidence_budget':{'unit':'chars','limit':budget_chars,'exact_reader_tokens':False},
        'candidate_limit':8,'top_k':4,'selector':'evidence_coverage','expand_graph':False,'reader_cases':list(reader_cases),
        'max_calls':max_calls,'model':settings.main.model,'embedding_model':Path(settings.embedding.model).name,
        'scope':'direct fixture admission; production access/selection/Reader; not extraction or real quality'}
    write_json(directory/'manifest.json',manifest)
    rows=[];calls=[];maps={};source_map={};blocked=0
    llm=LLMClient(settings) if reader_cases else None
    if llm:
        original=llm.chat
        async def bounded(messages,**kwargs):
            nonlocal blocked
            if len(calls)>=max_calls:blocked+=1;raise RuntimeError('Explicit comparison call ceiling exhausted')
            call={'status':'attempted','messages':messages,'case':current_case,'method':current_method};calls.append(call)
            start=time.perf_counter()
            try:
                result=await original(messages,**kwargs)
                call.update(status='returned',content=result.content,usage=result.usage or None,finish_reason=result.finish_reason)
                return result
            except BaseException as exc:call['error']=type(exc).__name__;raise
            finally:
                call['elapsed_ms']=round((time.perf_counter()-start)*1000,3);write_json(directory/'calls.json',calls)
        llm.chat=bounded
    for index,case in enumerate(suite['cases']):
        current_case=case['id'];path=directory/case['id'];path.mkdir()
        configured=copy.deepcopy(settings);configured.database=path/'isolated.db'
        store=Store(configured.database,'json');kernel=MemoryKernel(configured,store,user_id='synthetic:entity-comparison')
        try:
            maps[case['id']],sources=await seed(kernel,case)
            source_map.update({case['id']+':'+key:{'domain':'conversation','id':mid} for key,mid in sources.items()})
            # Common query/model warmup excluded from paired retrieval latency and recorded separately.
            start=time.perf_counter();await kernel.embedder.embed_query(case['query']);warmup_ms=(time.perf_counter()-start)*1000
            ordered=METHODS[index%3:]+METHODS[:index%3]
            for current_method in ordered:
                row={'case':case['id'],'method':current_method,'corpus_ids':sorted(maps[case['id']].values()),'warmup_ms':round(warmup_ms,3)}
                start=time.perf_counter()
                try:
                    kernel.personal_access.engine=MethodEngine(kernel.memory,kernel.entities,current_method)
                    request=QueryRequest(query=case['query'],entity_type=case.get('entity_type'),mode='rrf',
                        use_entities=current_method!='three_lane_rrf',candidate_limit=8,top_k=4,selector='evidence_coverage',
                        budget_chars=budget_chars,include_experiences=False,use_answer_risk=False,use_adaptive_retrieval=False)
                    if case['id'] in reader_cases:
                        row['reader']=await kernel.answer(request)
                        row['access']=row['reader']['access']
                    else:
                        row['access']=await kernel.access(request)
                    row['retrieval_selection_ms']=row['access'].get('elapsed_ms')
                    row['status']='finished'
                    if row.get('reader',{}).get('validation',{}).get('valid') is False:
                        row.update(status='failed',error='ReaderValidationFailure')
                except Exception as exc:row.update(status='failed',error=type(exc).__name__)
                rows.append(row);write_json(path/(current_method+'.json'),row)
        except Exception as exc:
            rows.extend({'case':case['id'],'method':m,'status':'failed','error':type(exc).__name__} for m in METHODS)
        finally:store.close()
    write_json(directory/'source-map.json',source_map);write_json(directory/'predictions.json',rows)
    labels=json.loads((directory/'expectations.snapshot.json').read_text(encoding='utf-8'))
    if labels['data_kind']!='synthetic_functional' or set(labels['cases'])!={c['id'] for c in suite['cases']}:raise ValueError('Label coverage mismatch')
    for row in rows:
        if row['case'] in maps:row['metrics']=score(row,labels['cases'][row['case']],maps[row['case']])
    safety=all(r.get('metrics',{}).get('budget_ok') and not r['metrics']['revoked_exposed'] and not r['metrics']['invalid_entity_votes']
        and (not r['metrics']['clarification_expected'] or r['method']=='three_lane_rrf' or r['metrics']['clarification_observed']) for r in rows)
    summary={'data_kind':'synthetic_functional','cases':len(suite['cases']),'method_records':len(rows),
        'execution_failures':sum(r['status']!='finished' for r in rows),'safety_checks_passed':safety,
        'core_unchanged':core_fingerprint(settings.root)==fingerprint,'model_calls':len(calls),'blocked_calls':blocked,
        'failed_model_calls':sum(c['status']!='returned' for c in calls),
        'incomplete_model_calls':sum(c.get('finish_reason') not in {None,'stop'} for c in calls),
        'known_tokens':sum((c.get('usage') or {}).get('total_tokens',0) for c in calls),
        'unknown_usage_calls':sum(not c.get('usage') for c in calls),'production_writes':0}
    summary['passed']=len(rows)==len(suite['cases'])*3 and not summary['execution_failures'] and safety and summary['core_unchanged'] and not blocked
    write_json(directory/'results.json',{'summary':summary,'rows':rows})
    write_json(directory/'calls.json',calls)
    content='<html lang="zh"><meta charset="utf-8"><title>实体对照诊断</title><style>body{font:16px sans-serif;max-width:1200px;margin:40px auto}td,th{padding:10px;border:1px solid #ccc}pre{white-space:pre-wrap}</style><h1>实体召回同字符预算诊断</h1><p>虚构功能夹具、直接入库；不是抽取F1、真实质量或LoCoMo成绩。相同字符预算上限不等于相同实际Token。预算：'+str(budget_chars)+'</p><table><tr><th>场景</th><th>方法</th><th>必要证据：候选/选择/总数</th><th>错误实体</th><th>字符成本</th><th>Reader</th></tr>'
    for r in rows:
        m=r.get('metrics',{});access=r.get('access',{});selection=access.get('selection',{})
        cells=[r['case'],r['method'],f"{m.get('candidate_required')}/{m.get('selected_required')}/{m.get('required_count')}",
            str(len(m.get('wrong_entity_selected',[]))),str(selection.get('cost')),r.get('reader',{}).get('status',r['status'])]
        content+='<tr>'+''.join('<td>'+html.escape(c)+'</td>' for c in cells)+'</tr>'
    content+='</table><pre>'+html.escape(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre><p>逐方法JSON保留候选、选择轨迹、原话、依赖、引用、预算和耗时；calls.json保留请求和费用。0必要证据的题不计算召回率，所有失败保留分母。</p></html>'
    (directory/'report.html').write_text(content,encoding='utf-8')
    manifest.update(status='finished',artifacts={p.relative_to(directory).as_posix():digest_file(p) for p in directory.rglob('*') if p.is_file() and p.name!='manifest.json'})
    write_json(directory/'manifest.json',manifest)
    return {'directory':str(directory),**summary}
