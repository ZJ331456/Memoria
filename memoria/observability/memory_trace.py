"""Account-local lifecycle watermarks and frozen-pool diagnostics, never quality gold."""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar

_active_reads=ContextVar('production_memory_reads',default=None)


@contextmanager
def observe_reads():
    token=_active_reads.set([])
    try:yield _active_reads.get()
    finally:_active_reads.reset(token)


def record_read(request,candidates,selected):
    reads=_active_reads.get()
    if reads is not None and len(reads)<16:
        reads.append({'request_sha256':fingerprint(request),'candidates':candidates,'selected':selected})

def record_reranked_read(request,selected,reranking):
    reads=_active_reads.get()
    if reads and reads[-1]['request_sha256']==fingerprint(request):
        read=reads[-1];read['pre_rerank_selected']=read['selected'];read['selected']=selected
        read['reranking']={k:reranking.get(k) for k in ('status','calls','usage','elapsed_ms','reason')}


def read_visibility(messages):
    texts=[m.get('content','') for m in messages if isinstance(m.get('content'),str)]
    result=[]
    for read in _active_reads.get() or []:
        evidence=[]
        for record in read['selected']:
            content=record.get('content','');citation=record.get('citation','')
            encoded=json.dumps(content,ensure_ascii=False)[1:-1]
            visible=bool(citation and any(citation in t and (content in t or encoded in t) for t in texts))
            evidence.append({'citation':citation,'sources':candidate_sources(record),
                'literal_content_present':visible,'content_sha256':fingerprint(content)})
        result.append({'request_sha256':read['request_sha256'],'evidence':evidence,
                       'scope':'literal content/citation fragment presence only; full support packet not certified'})
    return result

STAGES = {
    'entity':('memory_entities','memory_entity_aliases','memory_entity_mentions','memory_entity_cohorts','memory_entity_events'),
    "source": ("messages", "memory_source_revocations", "dataset_imports","memory_source_clocks"),
    "extraction": ("memory_jobs","memory_extraction_diagnostics"),
    "formation": ("memory_formation_runs", "memory_observations", "memory_pattern_versions", "memory_formation_candidates","memory_formation_calls",
        "memory_observation_history","memory_formation_history_runs","memory_observation_semantics"),
    "review": ("memory_reviews","memory_review_assessments"),
    "memory": ("memories", "memory_operations", "memory_validity_intervals", "memory_source_dependencies",
               "memory_support_groups", "memory_support_evidence"),
    "index": ("memory_embeddings", "memory_embedding_chunks", "experience_versions", "experience_chunks", "experience_vectors"),
    "claim": ("claim_roots", "claim_revisions", "claim_generations"),
    "view": ("claim_views",),
    "library_source": ("library_sources","library_revisions","library_files","library_feedback"),
    "library_formation": ("library_formation_runs","library_observations","library_cards","library_calls"),
    "library_evolution": ("library_consolidations","library_consolidation_versions","library_consolidation_events"),
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def install(store):
    # Individual statements preserve any caller-owned transaction.
    store.db.execute("""CREATE TABLE IF NOT EXISTS memory_trace_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, stage TEXT NOT NULL, table_name TEXT NOT NULL,
        entity_id TEXT NOT NULL, operation TEXT NOT NULL, recorded_at TEXT NOT NULL)""")
    store.db.execute("""CREATE TABLE IF NOT EXISTS memory_trace_epochs (
        stage TEXT PRIMARY KEY, generation INTEGER NOT NULL)""")
    tables = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for stage, names in STAGES.items():
        store.db.execute("INSERT OR IGNORE INTO memory_trace_epochs VALUES(?,0)", (stage,))
        for table in names:
            if table not in tables:
                continue
            columns = [r[1] for r in store.db.execute(f"PRAGMA table_info({table})")]
            key = next((c for c in ("id", "memory_id", "message_id", "user_id","observation_id","revision_ref") if c in columns), columns[0])
            for operation in ("INSERT", "UPDATE", "DELETE"):
                alias = "OLD" if operation == "DELETE" else "NEW"
                when = " WHEN " + " OR ".join(f"OLD.{c} IS NOT NEW.{c}" for c in columns) if operation == "UPDATE" else ""
                store.db.execute(f"""CREATE TRIGGER IF NOT EXISTS trace_{table}_{operation.lower()}
                    AFTER {operation} ON {table}{when} BEGIN
                    INSERT INTO memory_trace_events(stage,table_name,entity_id,operation,recorded_at)
                    VALUES('{stage}','{table}',CAST({alias}.{key} AS TEXT),'{operation.lower()}',
                           strftime('%Y-%m-%dT%H:%M:%fZ','now'));
                    UPDATE memory_trace_epochs SET generation=generation+1 WHERE stage='{stage}'; END""")


def watermark(store):
    with store._memory_transaction():
        return {"schema_version": 1, "origin": "since_trace_schema_install; not lifetime history",
            "event_seq": store.db.execute("SELECT COALESCE(MAX(seq),0) FROM memory_trace_events").fetchone()[0],
            "epochs": dict(store.db.execute("SELECT stage,generation FROM memory_trace_epochs")),
            "embedding_namespace": store.embedding_namespace,
            "jobs": dict(store.db.execute("SELECT status,COUNT(*) FROM memory_jobs GROUP BY status")),
            "reviews": dict(store.db.execute("SELECT status,COUNT(*) FROM memory_reviews GROUP BY status"))}


def capture(request, result, owner, before, after, coverage_by_id):
    # Full candidates remain in the existing response exactly once; no second private text copy.
    trace = {"schema_version": 1, "owner_user_id": owner, "request": request,
        "before": before, "after": after, "coverage_by_id": coverage_by_id,
        "candidate_pool_sha256": fingerprint(result["candidates"]),
        "requirements_sha256": fingerprint(result["requirements"]),
        "selection_sha256": fingerprint(result["selection"]),
        "state_sha256": fingerprint(result["state"]),
        "request_sha256": fingerprint(request),
        "changed_stages": [s for s in before["epochs"] if before["epochs"][s] != after["epochs"][s]],
        "quality": "unscored", "gold_read": False,
        "replay_scope": "frozen candidate selection; not rerunning extraction or reader"}
    if 'reranking' in result:trace['reranking_sha256']=fingerprint(result['reranking'])
    if 'closure_advice' in result:trace['closure_advice_sha256']=fingerprint(result['closure_advice'])
    trace["trace_sha256"] = fingerprint(trace)
    return trace


def replay(packet, owner, *, budgets=None):
    from ..memory.evidence import EvidenceBudget
    from ..memory.evidence_selector import select_evidence
    trace = packet["production_trace"]
    if trace["owner_user_id"] != owner:
        raise ValueError("Production capture belongs to another account")
    if trace["schema_version"] != 1 or fingerprint({k:v for k,v in trace.items() if k != "trace_sha256"}) != trace["trace_sha256"]:
        raise ValueError("Production trace fingerprint mismatch")
    for key, value in (("candidate_pool", packet["candidates"]), ("requirements", packet["requirements"]),
                       ("selection", packet["selection"]), ("state", packet["state"]), ("request",trace["request"])):
        if fingerprint(value) != trace[key+"_sha256"]:
            raise ValueError("Production capture fingerprint mismatch: "+key)
    request = trace["request"]
    if request["budget_unit"] == "reader_tokens":
        raise ValueError("Offline replay needs the original trusted reader tokenizer; tokens cannot be guessed")
    if 'closure_advice_sha256' in trace:
        from ..memory.evidence.closure import select_closed
        advice = packet.get('closure_advice')
        if fingerprint(advice) != trace['closure_advice_sha256']:
            raise ValueError('Closure advice changed since capture')
        catalog = {c['id']: c for c in packet['candidates']}
        frozen = [catalog[mid] for mid in advice['candidate_ids']]
        selections = {str(limit): {'dependency_closed': select_closed(frozen, advice['decision'], advice['citation_owners'],
            EvidenceBudget(limit, request['budget_unit']), limit=request['top_k'])}
            for limit in (budgets or [request['evidence_budget'] or request['budget_chars']])}
        return {'selections': selections, 'candidate_pool_sha256': trace['candidate_pool_sha256'], 'model_calls': 0,
            'automatic_memory_writes': 0, 'quality': 'unscored',
            'replay_scope': 'frozen coverage advice and dependency closure; not live source/LLM validation'}
    if set(trace["coverage_by_id"]) != {c["id"] for c in packet["candidates"]}:
        raise ValueError("Captured coverage does not match candidate IDs")
    pool = [{**c, "covers":trace["coverage_by_id"][c["id"]]} for c in packet["candidates"]]
    if 'reranking_sha256' in trace:
        advice=packet.get('reranking')
        if fingerprint(advice)!=trace['reranking_sha256']:raise ValueError('Reranking capture fingerprint mismatch')
        if advice['status']=='applied':
            order=[r['id'] for r in advice['decision']['ranking']]
            if len(order)!=len(pool) or set(order)!={c['id'] for c in pool}:raise ValueError('Reranking pool mismatch')
            scores={mid:len(order)-i for i,mid in enumerate(order)}
            pool=[{**c,'score':scores[c['id']]} for c in pool]
    limits = budgets or [request.get("evidence_budget") or request["budget_chars"]]
    if len(limits) > 8 or any(type(b) is not int or not 256 <= b <= 16000 for b in limits):
        raise ValueError("Use at most eight evidence budgets in 256..16000")
    result = {}
    for limit in limits:
        budget = EvidenceBudget(limit, request["budget_unit"])
        result[str(limit)] = {strategy: select_evidence(pool, packet["requirements"]["facets"], limit,
            strategy, request["top_k"], budget=budget)
            for strategy in ("topk", "coverage", "mmr", "evidence_coverage")}
    return {"selections":result, "candidate_pool_sha256":trace["candidate_pool_sha256"],
            "model_calls":0, "automatic_memory_writes":0, "quality":"unscored",
            "budget_scope":"complete evidence JSON; excludes reader/system/output"}


def attribute(packet, *, required_source_ids=None, source_inventory=None, reader=None, answer_correct=None):
    """Post-hoc diagnostic only. Label IDs NEVER enter retrieval or selection."""
    required = set(required_source_ids or [])
    if not required:
        return {"stage":"unlabelled", "quality":"unknown", "semantic_sufficiency":None}
    retrieved = {s for c in packet["candidates"] for s in c["source_ids"]}
    presented = {s for c in packet["selection"]["payload"]["evidence"] for s in c["source_ids"]}
    if source_inventory is not None and not required <= set(source_inventory):
        stage = "source_not_written_or_unavailable"
    elif packet.get("state") and not packet["state"]["payload"]["complete"]:
        stage = "state_not_approved_complete_or_applicable"
    elif not required <= retrieved:
        stage = "not_retrieved"
    elif not required <= presented:
        stage = "candidate_not_presented"
    elif reader is None:
        stage = "presented_reader_not_run"
    elif reader.get("status") == "error":
        stage = "reader_execution_failure"
    elif not reader.get("validation", {}).get("valid", True):
        stage = "reader_contract_or_support_failure"
    elif answer_correct is False:
        stage = "reader_answer_or_support_failure"
    elif reader.get("status") != "answer":
        stage = "reader_abstained_or_clarified"
    else:
        stage = "answered_correct" if answer_correct is True else "answered_unscored"
    return {"stage":stage, "required_source_count":len(required),
            "missing_retrieved":sorted(required-retrieved), "missing_presented":sorted(required-presented),
            "semantic_sufficiency":None, "interpretation":"source-ID coverage diagnostic, not semantic gold"}


def execution_failure(exc,phase):
    """Safe operational class: never serialize provider response bodies or keys."""
    chain=[];current=exc
    while current is not None and len(chain)<5:
        chain.append(type(current).__name__)
        current=current.__cause__
    transport={"ConnectError","ConnectTimeout","ReadTimeout","ReadError","WriteError","RemoteProtocolError","PoolTimeout"}
    stage=("call_budget_exhausted" if "CallCeiling" in chain else
           "reader_transport_failure" if transport.intersection(chain) else
           "reader_provider_failure" if "ProviderError" in chain else
           "reader_incomplete_output" if "IncompleteResponseError" in chain else
           "reader_budget_failure" if phase=="budget" else
           "reader_contract_failure" if phase=="parse" else "reader_execution_failure")
    prefix={'message':'formation','source':'source_formation','form':'source_formation',
            'feedback':'feedback','revision':'revision','retract':'retraction'}.get(phase)
    if prefix and stage.startswith('reader_'):stage=prefix+stage[len('reader'):]
    return {"failure_stage":stage,"error_type":type(exc).__name__,"cause_types":chain}


def candidate_sources(candidate):
    """Use actual evidence spans, not timeline support-group IDs masquerading as messages."""
    if candidate.get('kind') == 'library' and isinstance(candidate.get('data'), dict):
        return candidate_sources(candidate['data'])
    if 'domain' in candidate:
        if candidate['representation']=='consolidated_memory':
            return [{'domain':'library','id':s['source_id']} for s in candidate['provenance']['support']]
        return [{'domain':candidate['domain'],'id':candidate['source_id']}]
    result=[]
    data=candidate.get('data',[])
    for item in data if isinstance(data,list) else [data]:
        spans=item.get('evidence',[])
        if isinstance(spans,dict):spans=[spans]
        for e in spans+item.get('condition_evidence',[]):
            ref={'domain':'conversation','id':e['message_id']}
            if ref not in result:result.append(ref)
    return result


def source_lineage(kernel, references):
    """Current bounded metadata snapshot. No model, approval, source text or historic reconstruction."""
    if len(references)>64:raise ValueError('At most 64 source references per lineage snapshot')
    store=kernel.store;result=[]
    def rows(sql,args):return [dict(r) for r in store.db.execute(sql,args)]
    with store.lock:
        for ref in references:
            domain,sid=ref['domain'],ref['id']
            item={'domain':domain,'id':sid,'available':False,'observations':[],'reviews':[], 'published':[]}
            if domain=='memory':
                memory=store.memory(sid)
                if memory:
                    source=store.memory_source(sid)
                    item.update(available=store.memory_dependency_valid(store.memory_dependency(memory)),
                        content_sha256=hashlib.sha256(memory['content'].encode()).hexdigest(),status=memory['status'],
                        source_ref=source['message_id'] if source else None)
                    from ..memory.source_support import live_groups
                    groups=live_groups(store,sid)
                    item['support_groups']=[{'id':g['id'],'origin':g['origin'],
                        'message_ids':[e['message_id'] for e in g['evidence']]} for g in groups]
                    refs=list(dict.fromkeys(e['message_id'] for g in groups for e in g['evidence']))
                    item['support_lineage_truncated']=len(refs)>64
                    item['support_lineage']=source_lineage(kernel,[{'domain':'conversation','id':mid} for mid in refs[:64]])
            elif domain=='conversation':
                source=store.message_source(sid)
                if source:
                    try:
                        from ..memory.formation_store import eligible_source
                        eligible_source(store,sid);eligible=True
                    except ValueError:eligible=False
                    origin=store.db.execute('SELECT channel,event_ref FROM message_origins WHERE message_id=?',(sid,)).fetchone()
                    item.update(available=store.memory_dependency_valid(store.message_dependency(source)),
                        personal_claim_eligible=eligible,role=source['role'],session_id=source['session_id'],
                        recorded_at=source['created_at'],source_sha256=hashlib.sha256(source['content'].encode()).hexdigest(),
                        origin=dict(origin) if origin else {'channel':'unknown'})
                    from ..memory.observation_semantics import message_clock,public_clock
                    item['source_clock']=public_clock(message_clock(store,source))
                    item['jobs']=rows('SELECT id,status,attempts,extracted_count,staged_count FROM memory_jobs WHERE source_ref=?',(sid,))
                    observations=rows('''SELECT id,run_id,category,topic,scope,polarity,start,end,asserted,durable,state,
                        source_sha,observed_at FROM memory_observations WHERE message_id=? ORDER BY rowid LIMIT 64''',(sid,))
                    from ..memory.formation_history import visible
                    for observation in observations:
                        from ..memory.observation_semantics import load
                        observation['semantics']=load(store,'conversation',observation['id'])
                        try:observation['history']=visible(store,observation['id'])
                        except ValueError:observation['history']={'association':{'status':'stale'}}
                    item['observations']=observations
                    for review in rows('SELECT id FROM memory_reviews WHERE source_ref=? ORDER BY rowid LIMIT 64',(sid,)):
                        review=store.memory_review(review['id'])
                        audit={k:review.get(k) for k in ('id','status','applied_memory_id','applied_claim_id')}
                        assessment=review.get('assessment')
                        if assessment:
                            assessment=dict(assessment)
                            proposal=assessment.get('structured_proposal')
                            if proposal:
                                assessment['structured_proposal']={k:v for k,v in proposal.items() if k not in {'evidence','value'}}|{
                                    'value_sha256':fingerprint(proposal.get('value')),
                                    'evidence':[{k:v for k,v in e.items() if k!='quote'} for e in proposal['evidence']]}
                        audit['assessment']=assessment;item['reviews'].append(audit)
                    for review in item['reviews']:
                        if review['applied_memory_id']:
                            memory=store.memory(review['applied_memory_id'])
                            if memory:item['published'].append({'kind':'memory','id':memory['id'],'status':memory['status'],
                                'content_sha256':fingerprint(memory['content']), 'origin':memory['source']})
                    for row in rows('''SELECT DISTINCT r.id,r.claim_id,r.revision,r.status,r.recorded_at,r.data_json
                        FROM claim_sources s JOIN claim_revisions r ON r.id=s.revision_id WHERE s.message_id=?
                        ORDER BY r.recorded_at,r.revision LIMIT 64''',(sid,)):
                        data=json.loads(row.pop('data_json'));row['kind']='claim'
                        head=kernel.claims.get(row['claim_id'])
                        row['current']=head['revision_id']==row['id']
                        row['admission_origin']='llm' if any(
                            (r.get('assessment') or {}).get('applied_claim_revision_id')==row['id']
                            for r in item['reviews']) else 'unknown_from_legacy_metadata'
                        row['source_available']=head['source_available'] if row['current'] else None
                        row['identity']={k:head.get(k) for k in ('subject_id','predicate','scope')}
                        row['identity']['polarity']=data.get('polarity')
                        row['value_sha256']=fingerprint(data.get('value'));item['published'].append(row)
                    for pattern in rows('''SELECT DISTINCT p.id,p.memory_id,p.status,p.action,p.previous_id,p.expires_at
                        FROM memory_pattern_versions p JOIN json_each(p.observation_ids_json) j
                        JOIN memory_observations o ON o.id=j.value WHERE o.message_id=? LIMIT 64''',(sid,)):
                        item['published'].append({'kind':'pattern',**pattern})
            elif domain=='library':
                source=store.db.execute('SELECT * FROM library_sources WHERE id=?',(sid,)).fetchone()
                if source:
                    raw=kernel.library._revision(source['head_id'])
                    from ..memory.observation_semantics import library_clock,public_clock
                    item['source_clock']=public_clock(library_clock(kernel.library,dict(source),raw))
                    item.update(available=source['status']=='active',kind=source['kind'],authorship=source['authorship'],
                        scope=source['scope'],session_id=source['session_id'],revision_id=raw['id'],
                        source_sha256=raw['sha'],recorded_at=raw['created_at'])
                    item['observations']=rows('''SELECT o.id,o.card_id,o.revision_id,o.group_json,c.status,c.start,c.end,
                        o.subject_start,o.subject_end FROM library_observations o JOIN library_cards c ON c.id=o.card_id
                        WHERE o.source_id=? ORDER BY o.rowid LIMIT 64''',(sid,))
                    from ..memory.observation_semantics import load
                    for observation in item['observations']:observation['semantics']=load(store,'library',observation['id'])
                    item['reviews']=[{'id':r['card_id'],'status':r['status']} for r in item['observations']]
                    item['published']=[{'kind':'source_observation','id':r['id'],'revision_id':r['revision_id'],
                        'available':kernel.library.formation.card_available(r['card_id'])} for r in item['observations']]
                    for version in rows('''SELECT DISTINCT v.id,v.root_id,v.previous_id,v.action,v.status,v.created_at,v.support_json
                        FROM library_consolidation_versions v JOIN json_each(v.context_json) j
                        WHERE json_extract(j.value,'$.id')=? ORDER BY v.created_at LIMIT 64''',(sid,)):
                        root=store.db.execute('SELECT head_id,status FROM library_consolidations WHERE id=?',(version['root_id'],)).fetchone()
                        item['published'].append({'kind':'consolidation',**version,'support_groups':json.loads(version['support_json']),
                            'current':bool(root and root['head_id']==version['id']), 'root_status':root['status'] if root else None})
                    item['calls']=rows('SELECT id,stage,model,usage_json,status,duration_ms FROM library_calls WHERE revision_id=? LIMIT 128',(raw['id'],))
            else:raise ValueError('Unknown lineage source domain')
            item['snapshot_scope']='current metadata; no retroactive source reconstruction'
            result.append(item)
    return result


def answer_trace(kernel, request, result, *, calls=None):
    """Freeze the actual route/candidates/selection; labels are never an input."""
    access=result.get('access',{})
    candidates=access.get('candidates',access.get('evidence',[]))
    selected=access.get('selection',{}).get('payload',{}).get('evidence',access.get('evidence',[]))
    def summarize(c):
        data=c.get('data',[]);data=data if isinstance(data,list) else [data]
        return {'id':c.get('id',c.get('citation')),'kind':c.get('kind',c.get('representation')),
            'sources':candidate_sources(c),'payload_sha256':fingerprint(c),
            'source_revision_id':c.get('provenance',c.get('data',{}).get('provenance',{}) if isinstance(c.get('data'),dict) else {}).get('revision_id'),
            'claim_revisions':[{'id':d['id'],'revision_id':d['revision_id'],'subject_id':d.get('subject_id'),
                'predicate':d.get('predicate')} for d in data if 'revision_id' in d and 'predicate' in d]}
    pool=[summarize(c) for c in candidates]; chosen=[summarize(c) for c in selected]
    references=[]
    for c in pool:
        for ref in c['sources']:
            if ref not in references:references.append(ref)
    for proposal in access.get('pending_revisions',[]):
        ref={'domain':'conversation','id':proposal['source_ref']}
        if ref not in references:references.append(ref)
    state=access.get('state')
    state_snapshot=({k:state.get(k) for k in ('path','reason','view_id','compiled_view_id')}|{
        'complete':state['payload']['complete'],'missing':state['payload']['missing'],
        'claim_revision_ids':[c['revision_id'] for c in state['payload']['claims']]} if state else None)
    citations=[]
    for citation in result.get('citations',[]):
        if 'provenance' not in citation:citations.append(citation)
        else:citations.append({k:v for k,v in citation.items() if k!='provenance'}|{
            'provenance_sha256':fingerprint(citation['provenance']),
            'source_revision_id':citation['provenance'].get('revision_id')})
    trace={'schema_version':2,'owner_user_id':kernel.claims.user_id,'request_sha256':fingerprint(request),
        'subject_id':request.get('subject_id'),'route':access.get('path','knowledge'),
        'state':state_snapshot,'routing':access.get('routing'),'pending_revisions':access.get('pending_revisions',[]),
        'sources':source_lineage(kernel,references[:64]),'source_inventory_truncated':len(references)>64,
        'candidates':pool,'selected':chosen,'selection':{k:v for k,v in access.get('selection',{}).items() if k!='payload'},
        'reader':{'called':result.get('reader_called',False),'status':result.get('status'),
            'budget':result.get('reader_budget'),'usage':result.get('usage'), 'calls':calls or [],
            'validation':result.get('validation'),'citations':citations},
        'reranking':{k:access.get('reranking',{}).get(k) for k in ('status','calls','usage','elapsed_ms','reason','decision')},
        'adaptive':{k:access.get('adaptive',{}).get(k) for k in ('version','query_plan','retrieval_rounds','rounds','policy','unknown_usage_count','elapsed_ms')},
        'answer_sha256':fingerprint(result.get('answer','')),
        'quality':'unscored','gold_read':False,'semantic_correctness':None}
    trace['trace_sha256']=fingerprint(trace)
    return trace
