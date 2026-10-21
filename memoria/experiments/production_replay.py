"""Private production diagnostics: frozen selection or explicitly bounded full-chain execution."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

from ..claims import ClaimMemory
from ..memory import MemoryEngine, EmbeddingClient
from ..memory.access import MemoryAccessOrchestrator, QueryRequest
from ..observability.memory_trace import replay, watermark
from ..store import Store
from .history import canonical, write_json
from .records import RunRecorder

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from ..memory.library import SourceInput,FeedbackInput,RevisionInput
from ..memory.records import MemorySearch,KnowledgeAnswer


class Step(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    id: str=Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')


class MessageStep(Step):
    kind: Literal['message']
    session: str=Field(min_length=1,max_length=64)
    actor: str=Field(min_length=1,max_length=64)
    role: Literal['user','assistant']
    content: str=Field(min_length=1,max_length=12000)
    original_time: str | None=None


class SourceStep(Step):
    kind: Literal['source']
    body: SourceInput
    form: bool=True


class ChangeStep(Step):
    kind: Literal['feedback','revision','retract']
    source: str=Field(min_length=1,max_length=64)
    content: str=Field(default='',max_length=120000)
    title: str=Field(default='',max_length=160)
    outcome: Literal['success','failure','unknown']='unknown'


class QuestionStep(Step):
    kind: Literal['question']
    target: Literal['personal','knowledge']
    request: dict

    @model_validator(mode='after')
    def validate_request(self):
        model=QueryRequest if self.target=='personal' else KnowledgeAnswer
        body=model.model_validate(self.request)
        if body.session_id is not None:raise ValueError('Run questions do not accept real session IDs')
        if self.target=='personal' and (body.predicates or body.context or body.subject_id!='self' or body.fact_as_of or body.recorded_as_of):
            raise ValueError('Automatic closed-loop queries must derive slots, conditions and time from the question; no gold-filled routing')
        return self


class FormStep(Step):
    kind: Literal['form']
    source: str=Field(min_length=1,max_length=64)
    start: int=Field(default=0,ge=0)
    max_chars: int=Field(default=12000,ge=1,le=12000)


class ClosedLoopInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    schema_version: Literal[1]=1
    provenance: str=Field(min_length=1,max_length=1000)
    allowed_purposes: list[Literal['memory_processing','evaluation']]=Field(min_length=2,max_length=2)
    subject_key: str | None=Field(default=None,min_length=1,max_length=64)
    events: list[Annotated[MessageStep|SourceStep|ChangeStep|FormStep|QuestionStep,Field(discriminator='kind')]]=Field(min_length=1,max_length=64)

    @model_validator(mode='after')
    def chronology(self):
        if set(self.allowed_purposes)!={'memory_processing','evaluation'}:raise ValueError('Both purposes must be declared')
        seen={};questions=0
        for event in self.events:
            if event.id in seen:raise ValueError('Duplicate event ID')
            if isinstance(event,MessageStep):
                if event.role=='user' and event.actor!=self.subject_key:
                    raise ValueError('A first-person message must match the explicitly bound subject; use separate runs for other people')
                if event.original_time:
                    from .history import timestamp
                    timestamp(event.original_time)
            elif isinstance(event,SourceStep):
                if event.body.scope!='personal' or event.body.session_id is not None:
                    raise ValueError('Replay source bodies cannot reference production/working sessions')
            elif isinstance(event,FormStep):
                if event.source not in seen or not isinstance(seen[event.source],SourceStep):
                    raise ValueError('Formation must reference an earlier library source')
            elif isinstance(event,ChangeStep):
                if event.source not in seen or not isinstance(seen[event.source],(MessageStep,SourceStep)):
                    raise ValueError('Changes must reference an earlier source')
                if event.kind!='retract' and not isinstance(seen[event.source],SourceStep):
                    raise ValueError('Feedback/revision target must be a library source')
                if event.kind!='retract' and not event.content.strip():raise ValueError('Change requires real source text')
            else:questions+=1
            seen[event.id]=event
        if not 1<=questions<=10:raise ValueError('Use 1..10 questions per bounded production run')
        return self


class CallCeiling(RuntimeError):
    pass


def prepare_locomo_probe(workspace, input_path, *, version='v2'):
    """Materialize the fixed, single-speaker diagnostic without reading QA/gold into a run.

    LoCoMo characters are constructed benchmark subjects, never the account owner.
    Only exact released text is used; dates are kept as raw provenance, not backdated.
    This is deliberately separate from the official full benchmark protocol.
    """
    from .locomo import SOURCE_SHA256, SOURCE_URL
    from .records import digest_file
    if version not in {'v1', 'v2'}:
        raise ValueError('Unknown production probe version')
    name = 'production-memory-locomo-probe' + ('-v2' if version == 'v2' else '') + '-20261020.json'
    protocol_path = Path(__file__).resolve().parents[2] / 'eval/protocols' / name
    protocol = json.loads(protocol_path.read_text(encoding='utf-8'))
    input_path = Path(input_path)
    if digest_file(input_path) != SOURCE_SHA256 or protocol['source_sha256'] != SOURCE_SHA256:
        raise ValueError('Official LoCoMo source fingerprint mismatch')
    record = json.loads(input_path.read_text(encoding='utf-8'))[protocol['conversation_index']]
    conversation = record['conversation']
    turns = [m for m in conversation[protocol['session']] if m['dia_id'] == protocol['dia_id']]
    if len(turns) != 1 or turns[0]['speaker'] != protocol['speaker']:
        raise ValueError('Fixed source identity or turn mismatch')
    turn = turns[0]
    actor = record['sample_id'] + ':' + protocol['speaker']
    provenance = {'source_url': SOURCE_URL, 'source_sha256': SOURCE_SHA256,
        'sample_id': record['sample_id'], 'speaker': protocol['speaker'], 'dia_id': turn['dia_id'],
        'session': protocol['session'], 'session_date_raw': conversation[protocol['session'] + '_date_time'],
        'text_sha256': hashlib.sha256(turn['text'].encode('utf-8')).hexdigest(),
        'protocol_sha256': digest_file(protocol_path), 'source_kind': protocol['source_kind'],
        'qa_gold_used': False, 'identity_policy': protocol['identity_policy']}
    events = [{'id': 'source_turn', 'kind': 'message', 'session': protocol['session'],
        'actor': actor, 'role': 'user', 'content': turn['text']}]
    for question in protocol['questions']:
        if question['id'] == protocol['withdraw_before']:
            events.append({'id': 'withdraw', 'kind': 'retract', 'source': 'source_turn'})
        events.append({'id': question['id'], 'kind': 'question', 'target': 'personal',
            'request': {k: v for k, v in question.items() if k != 'id'} | protocol['request_limits']})
    document = ClosedLoopInput(provenance=(protocol['id'] + '; official constructed benchmark, not real personal history; '
        + SOURCE_SHA256 + '; ' + record['sample_id'] + '/' + protocol['dia_id'] + '; protocol=' + digest_file(protocol_path)),
        allowed_purposes=['memory_processing', 'evaluation'], subject_key=actor, events=events)
    files = {'input.json': canonical(document.model_dump(mode='json')) + '\n',
        'source-provenance.json': canonical(provenance) + '\n', 'protocol.json': canonical(protocol) + '\n'}
    target = workspace.path('research', 'benchmarks', protocol['id'])
    if target.exists():
        if any((target / name).read_bytes() != value.encode('utf-8') for name, value in files.items()):
            raise ValueError('Existing probe bundle differs; never overwrite frozen input')
    else:
        target = workspace._publish(('benchmarks', protocol['id']), files)
    return target / 'input.json'


class CaseJudgment(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    case_id: str=Field(min_length=1,max_length=64)
    support_groups: list[list[str]]=Field(default_factory=list,max_length=8)
    required_route: Literal['any','claim','pattern','source','library']='any'
    stage_judgments: dict[Literal['extraction','publication','retrieval','selection','reader','answer'],bool]=Field(default_factory=dict)

    @model_validator(mode='after')
    def groups(self):
        if any(not 1<=len(g)<=16 or len(set(g))!=len(g) for g in self.support_groups):
            raise ValueError('Support groups must be nonempty sets of at most 16 source keys')
        return self


def attribute_closed_loop(case, judgment):
    """Earliest observable failure, with explicit unknown upstream semantics. No oracle retrieval."""
    label=CaseJudgment.model_validate(judgment);prefix=case['prefix']
    if label.case_id!=case['id']:raise ValueError('Case judgment ID mismatch')
    if any(k not in prefix for group in label.support_groups for k in group):
        raise ValueError('Gold references a future or absent prefix source')
    result=case.get('result',{});trace=result.get('closed_loop',{})
    def identity(ref):return (ref['domain'],ref['id'])
    retrieved={identity(r) for c in trace.get('candidates',[]) for r in c['sources']}
    selected={identity(r) for c in trace.get('selected',[]) for r in c['sources']}
    def covered(keys):return any(all(identity(prefix[k]) in keys for k in g) for g in label.support_groups)
    available={identity(p) for p in prefix.values() if p['lineage']['available']}
    groups=label.support_groups
    observations={k:len(p['lineage']['observations']) for k,p in prefix.items()}
    publication=[]
    for key,p in prefix.items():
        if label.required_route=='claim':
            ok=any(a['kind']=='claim' and a['status']=='approved' and a.get('current') and a.get('source_available')
                   for a in p['lineage']['published'])
        elif label.required_route=='pattern':
            ok=any(a['kind']=='pattern' and a['status']=='active' for a in p['lineage']['published'])
        else:ok=p['lineage']['available']
        if ok:publication.append(identity(p))
    if case['status']=='error':stage=case.get('failure',{}).get('failure_stage','execution_failure')
    elif groups and not covered(available):stage='source_unavailable'
    else:
        stages=('extraction','publication','retrieval','selection','reader','answer')
        stage=next((s for s in stages if label.stage_judgments.get(s) is False),None)
        if stage is None:
            stage=('revision_review_pending' if trace.get('pending_revisions') else
                   'publication_or_governance_gap' if groups and label.required_route in {'claim','pattern'} and not covered(set(publication)) else
                   'not_retrieved' if groups and not covered(retrieved) else
                   'candidate_not_selected' if groups and not covered(selected) else
                   'reader_contract_failure' if result.get('validation',{}).get('valid') is False else
                   'declared_correct_nonanswer' if result.get('status') in {'clarify','abstain'} and label.stage_judgments.get('answer') is True else
                   'reader_not_called' if not result.get('reader_called') else
                   'reader_abstained_or_clarified' if result.get('status')!='answer' else
                   'answered_correct_by_declared_label' if label.stage_judgments.get('answer') is True else 'answered_unscored')
    return {'case_id':case['id'],'stage':stage,'route':trace.get('route'),
        'declared_answer_correct':label.stage_judgments.get('answer'),
        'observations_by_source':observations,'support_group_coverage':{'source_available':covered(available) if groups else None,
            'retrieved':covered(retrieved) if groups else None,'selected':covered(selected) if groups else None},
        'unknown_semantic_stages':[s for s in ('extraction','publication','retrieval','selection','reader','answer') if s not in label.stage_judgments],
        'semantic_sufficiency':label.stage_judgments.get('selection'),
        'interpretation':'post-hoc declared labels and structural funnel; ID coverage alone is not semantic sufficiency or causal proof'}


def attribute_closed_loop_file(workspace,settings,source_run,judgments_path):
    from .history import identifier
    from .records import digest_file
    source=workspace.path('experiments',identifier(source_run));manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    if (manifest['protocol'].get('mode')!='production_closed_loop_v1' or
        manifest['protocol']['owner_user_id']!=workspace.owner or manifest['status']=='running'):
        raise ValueError('Only a finished closed-loop run from this account can be attributed')
    for name,info in manifest['artifacts'].items():
        if Path(name).name!=name or digest_file(source/name)!=info['sha256']:raise ValueError('Source artifact fingerprint mismatch')
    labels=json.loads(Path(judgments_path).read_text(encoding='utf-8-sig'))
    if set(labels)!={'reviewer','provenance','cases'} or not labels['reviewer'] or not labels['provenance']:
        raise ValueError('Judgments require reviewer and honest annotation provenance')
    cases=[json.loads(line) for line in (source/'cases.jsonl').read_text(encoding='utf-8').splitlines()]
    by_id={CaseJudgment.model_validate(c).case_id:c for c in labels['cases']}
    if len(by_id)!=len(labels['cases']) or set(by_id)!={c['id'] for c in cases}:
        raise ValueError('One judgment per original case, including errors and unscored cases, is required')
    rows=[attribute_closed_loop(c,by_id[c['id']]) for c in cases]
    recorder=RunRecorder(workspace.path('experiments'),settings.root,
        datasets=[source/'manifest.json',Path(judgments_path)],models={},
        protocol={'owner_user_id':workspace.owner,'mode':'production_closed_loop_attribution_v1','source_run':source_run,
                  'reviewer':labels['reviewer'],'annotation_provenance':labels['provenance'],'post_hoc_only':True},seed=None)
    from collections import Counter
    for row in rows:recorder.append('cases.jsonl',row)
    recorder.finish({'cases':len(rows),'stage_counts':dict(Counter(r['stage'] for r in rows)),'model_calls':0,'errors':0,
                     'quality':'declared label diagnostics; no benchmark F1 inferred'})
    return recorder.path


async def execute_closed_loop(workspace,settings,input_path,*,execute=False,allow_remote=False,
                              max_calls=12,max_input_chars=120000,local_embeddings=False,client_factory=None,
                              synthetic=False,output_root=None):
    """Re-execute actual Kernel/worker/admission/Reader on a new DB, never copy production history.

    No gold, manual approval or alternate PrefixRetriever. Explicit synthetic functional
    diagnostics use a labelled, separate output root and never become research scores.
    Model-dependent runs retain errors and unexecuted questions in the denominator.
    """
    import copy,time
    from ..llm import LLMClient
    from ..memory.kernel import MemoryKernel
    from ..memory.source_formation import FormRequest
    from ..models_config import apply_account_settings
    from ..provenance import message_ingress
    from ..observability.memory_trace import fingerprint,execution_failure
    input_path=Path(input_path)
    if input_path.stat().st_size>1048576:raise ValueError('Closed-loop input exceeds 1 MiB')
    document=ClosedLoopInput.model_validate_json(input_path.read_text(encoding='utf-8-sig'))
    if type(synthetic) is not bool:raise ValueError('Synthetic marker must be boolean')
    if synthetic:
        if not document.provenance.startswith('[SYNTHETIC]') or (document.subject_key and not document.subject_key.startswith('synthetic:')):
            raise ValueError('Synthetic diagnostics require explicit provenance and synthetic subject binding')
        output_root=Path(output_root or settings.root/'simulation/runs').resolve()
        expected_root=settings.root.resolve()/'simulation/runs'
        if (settings.root/'simulation/runs').resolve()!=expected_root or not output_root.is_relative_to(expected_root):
            raise ValueError('Synthetic outputs must stay in simulation/runs')
    elif output_root is not None:raise ValueError('Custom output is only permitted for isolated synthetic diagnostics')
    if type(max_calls) is not int or not 1<=max_calls<=32:raise ValueError('Use 1..32 model calls')
    if type(max_input_chars) is not int or not 1000<=max_input_chars<=500000:raise ValueError('Input ceiling must be 1000..500000 characters')
    if not execute:return {'status':'validated_not_executed','model_calls':0,'questions':sum(isinstance(s,QuestionStep) for s in document.events)}
    configured=copy.deepcopy(settings)
    apply_account_settings(configured,workspace.directory)
    if not allow_remote and any(m.is_ready() and not(m.is_local_process() or m.is_local_http()) for m in (configured.main,configured.fast)):
        raise ValueError('Configured remote models require explicit --allow-remote')
    configured.max_retries=0
    configured.markdown_enabled=False
    # Isolated lexical profile is explicit; never silently charge a remote embedding endpoint.
    if local_embeddings:
        from ..memory.experience import local_encoder
        if not local_encoder(EmbeddingClient(configured.embedding)):raise ValueError('Only configured local BGE is allowed')
    else:
        from ..config import ModelConfig
        configured.embedding=ModelConfig(model='',api_key='',base_url='')
    recorder=RunRecorder(output_root if synthetic else workspace.path('experiments'),settings.root,datasets=[input_path],models={
        'main':{'model':configured.main.model,'thinking':configured.main.thinking,'max_tokens':1800},
        'fast':{'model':configured.fast.model,'thinking':configured.fast.thinking,'max_tokens':1800},
        'embedding':{'model':configured.embedding.model if local_embeddings else None}},
        protocol={'owner_user_id':workspace.owner,'mode':'synthetic_functional_v1' if synthetic else 'production_closed_loop_v1','max_calls':max_calls,
            'data_kind':'synthetic_functional' if synthetic else 'declared_source',
            'max_input_chars':max_input_chars,'max_output_tokens_per_call':1800,'max_retries':0,
            'retrieval_profile':'configured_local_embedding' if local_embeddings else 'explicit_lexical_no_embedding',
            'source_binding':'declared isolated subject; not independently verified','subject_key':document.subject_key,
            'time_policy':'original_time retained as provenance; runtime recorded time is not backdated; no clock simulation',
            'gold_read':False,'production_writes':0},seed=None)
    write_json(recorder.path/'input.json',document.model_dump(mode='json'))
    run_settings=copy.deepcopy(configured);run_settings.database=recorder.path/'isolated-memory.db'
    run_settings.max_tokens=min(run_settings.max_tokens,1800)
    store=Store(run_settings.database,'json');client=(client_factory or LLMClient)(run_settings)
    original=client.chat;calls=[];blocked=[];phase={'event':None,'stage':None};used_chars=0
    async def metered(messages,**kwargs):
        nonlocal used_chars
        text=json.dumps(messages,ensure_ascii=False,allow_nan=False)
        if len(calls)>=max_calls or used_chars+len(text)>max_input_chars:
            blocked.append(dict(phase));recorder.append('events.jsonl',{'kind':'call_budget_blocked',**phase})
            raise CallCeiling('Explicit run call/input ceiling reached')
        kwargs['max_tokens']=min(int(kwargs.get('max_tokens') or 1800),1800)
        cid=len(calls)+1;used_chars+=len(text);began=time.perf_counter()
        from ..memory.formation import OBSERVE_PROMPT,CONSOLIDATE_PROMPT
        from ..memory.auto_review import PROMPT as REVIEW_PROMPT
        from ..memory.source_formation import OBSERVE,VERIFY,CONSOLIDATE,CHECK_CONSOLIDATION
        from ..memory.rerank import SYSTEM as RERANK_SYSTEM
        stage={OBSERVE_PROMPT:'observation_extract',CONSOLIDATE_PROMPT:'pattern_consolidate',REVIEW_PROMPT:'memory_admission',
            OBSERVE:'source_observe',VERIFY:'source_verify',CONSOLIDATE:'source_consolidate',
            CHECK_CONSOLIDATION:'source_consolidation_verify',RERANK_SYSTEM:'evidence_rerank'}.get(messages[0]['content'],'reader' if phase['stage']=='question' else phase['stage'])
        if messages[0]['content'].startswith(REVIEW_PROMPT+'\n'):stage='memory_admission'
        entry={'id':cid,**phase,'stage':stage,'status':'started','usage':None,'input_chars':len(text),
            'input_sha256':fingerprint(messages),'max_tokens':kwargs['max_tokens'],'model':getattr(kwargs.get('model'),'model',None)}
        calls.append(entry)
        recorder.append('prompts.jsonl',{'call_id':cid,**phase,'stage':stage,'messages':messages})
        result=None
        try:
            result=await original(messages,**kwargs)
            entry.update(status='returned',usage=result.usage or None,finish_reason=result.finish_reason,
                tool_call_count=len(result.tool_calls),retries=result.retries,request_budget=result.budget)
            recorder.append('events.jsonl',{'kind':'model_return','call_id':cid,'content':result.content,
                'finish_reason':result.finish_reason})
            return result
        except BaseException as exc:
            entry.update(status='failed',failure=execution_failure(exc,phase['stage']))
            raise
        finally:
            entry['elapsed_ms']=round((time.perf_counter()-began)*1000,3)
            recorder.append('calls.jsonl',entry)
    client.chat=metered
    kernel=MemoryKernel(run_settings,store,client,user_id='benchmark:'+workspace.owner)
    if kernel.memory_worker is not None:kernel.memory_worker.max_retries=1
    sources={};sessions={};cases=[];prefix_failed=False;event_errors=0;failed_event_ids=set();metrics={'errors':0};fatal=None
    try:
        for step in document.events:
            phase.update(event=step.id,stage=step.kind)
            started=time.perf_counter()
            if isinstance(step,QuestionStep):
                previous_blocks=len(blocked)
                row={'id':step.id,'target':step.target,'question':step.request['query'],'status':'error',
                    'prefix':{key:{**ref,'lineage':kernel.lineage([ref])[0]} for key,ref in sources.items()},
                    'prefix_event_id':step.id,'prefix_failed':prefix_failed}
                if prefix_failed:row['failure']={'failure_stage':'prefix_execution_failed'}
                else:
                    try:
                        request=(QueryRequest if step.target=='personal' else KnowledgeAnswer).model_validate(step.request)
                        result=await kernel.answer(request)
                        row.update(status='completed',result=result)
                        reason=(result.get('validation') or {}).get('reason')
                        if reason in {'reader_provider_failure','reader_budget_exceeded','invalid_answer_schema',
                            'incomplete_or_tool_reader_output','source_packet_not_fully_presented','evidence_not_presented'}:
                            row.update(status='error',failure={'failure_stage':reason})
                        if len(blocked)>previous_blocks:
                            row.update(status='error',failure={'failure_stage':'call_budget_exhausted'})
                    except Exception as exc:row['failure']=execution_failure(exc,'reader')
                row['elapsed_ms']=round((time.perf_counter()-started)*1000,3)
                cases.append(row);recorder.append('cases.jsonl',row)
                continue
            if prefix_failed:
                recorder.append('events.jsonl',{'event_id':step.id,'status':'not_executed_after_prefix_failure'});continue
            try:
                previous_blocks=len(blocked)
                if isinstance(step,MessageStep):
                    sid=sessions.setdefault(step.session, None)
                    if sid is None:sid=sessions[step.session]=store.create_session('Isolated source session')['id']
                    with message_ingress('internal'):
                        message=kernel.record_message(sid,step.role,step.content)
                    sources[step.id]={'domain':'conversation','id':message['id']}
                    if step.role=='user':
                        if kernel.memory_worker is None:raise ValueError('Production memory worker unavailable')
                        job=store.enqueue_memory_job(message['id'],step.content,'')
                        await kernel.memory_worker.process_once()
                        current=store.db.execute('SELECT status FROM memory_jobs WHERE id=?',(job['id'],)).fetchone()
                        if current[0]!='completed':raise ValueError('Production formation job did not complete')
                elif isinstance(step,SourceStep):
                    source=kernel.ingest(step.body);sources[step.id]={'domain':'library','id':source['id']}
                    if step.form:
                        # Explicitly bounded, whole-source attempts; the production API itself tracks coverage.
                        for offset in range(0,min(len(step.body.content),96000),12000):
                            receipt=await kernel.library.formation.form(source['id'],FormRequest(start=offset,
                                expected_revision_id=source['revision']['id']))
                            recorder.append('events.jsonl',{'event_id':step.id,'formation':receipt})
                elif isinstance(step,FormStep):
                    ref=sources[step.source];old=kernel.library.detail(ref['id'])
                    receipt=await kernel.library.formation.form(ref['id'],FormRequest(start=step.start,max_chars=step.max_chars,
                        expected_revision_id=old['revision']['id']))
                    recorder.append('events.jsonl',{'event_id':step.id,'formation':receipt})
                else:
                    ref=sources[step.source]
                    if step.kind=='retract':
                        if ref['domain']=='conversation':store.undo_memory_sources([ref['id']])
                        else:kernel.library.retract(ref['id'])
                    else:
                        old=kernel.library.detail(ref['id'])
                        if step.kind=='feedback':kernel.library.feedback(ref['id'],FeedbackInput(
                            expected_revision_id=old['revision']['id'],outcome=step.outcome,content=step.content))
                        else:kernel.library.revise(ref['id'],RevisionInput(expected_revision_id=old['revision']['id'],
                            title=step.title or old['revision']['title'],content=step.content))
                if len(blocked)>previous_blocks:raise CallCeiling('Source processing exhausted the explicit budget')
                recorder.append('events.jsonl',{'event_id':step.id,'status':'completed',
                    'elapsed_ms':round((time.perf_counter()-started)*1000,3),
                    'source':sources.get(step.id),'original_time':getattr(step,'original_time',None)})
            except Exception as exc:
                prefix_failed=True;event_errors+=1;failed_event_ids.add(step.id)
                recorder.append('events.jsonl',{'event_id':step.id,'status':'failed','failure':execution_failure(exc,step.kind)})
        from collections import Counter
        counts=Counter(c.get('result',{}).get('status','error') for c in cases)
        def tokens(usage):
            if not isinstance(usage,dict):return None
            if type(usage.get('total_tokens')) is int:return usage['total_tokens']
            if all(type(usage.get(k)) is int for k in ('prompt_tokens','completion_tokens')):
                return usage['prompt_tokens']+usage['completion_tokens']
            return None
        totals=[tokens(c['usage']) for c in calls]
        known=sum(n for n in totals if n is not None)
        failed_calls=[c for c in calls if c['status']=='failed']
        incomplete_calls=[c for c in calls if c['status']=='returned' and (c.get('finish_reason') not in {None,'stop'} or c.get('tool_call_count'))]
        failure_events=failed_event_ids|{b['event'] for b in blocked}|{c['event'] for c in failed_calls+incomplete_calls}|{c['id'] for c in cases if c['status']=='error'}
        metrics={'questions':len(cases),'status_counts':dict(counts),
            'question_execution_status_counts':dict(Counter(c['status'] for c in cases)),
            'errors':len(failure_events),'source_event_errors':event_errors,
            'failed_model_calls':len(failed_calls),'incomplete_model_calls':len(incomplete_calls),
            'model_calls':len(calls),'known_total_tokens_lower_bound':known,'unknown_usage_calls':sum(n is None for n in totals),
            'input_chars':used_chars,'blocked_call_attempts':len(blocked),'quality':'synthetic_functional_not_research' if synthetic else 'unscored','gold_read':False,'production_writes':0,
            'structured_state_attempts':sum(c.get('result',{}).get('access',{}).get('state') is not None for c in cases),
            'claim_view_questions':sum(any(item.get('claim_revisions') for item in
                c.get('result',{}).get('closed_loop',{}).get('selected',[])) for c in cases),
            'compiled_view_questions':sum(bool((c.get('result',{}).get('access',{}).get('state') or {}).get('compiled_view_id')) for c in cases),
            'view_hit_questions':sum(c.get('result',{}).get('access',{}).get('path')=='view' for c in cases),
            'contract_rejections':sum(c.get('result',{}).get('validation',{}).get('valid') is False for c in cases),
            'limitations':['engineering execution is not semantic accuracy','no future source ingestion before a question',
                'source declaration is not independent consent/identity verification']}
        write_json(recorder.path/'source-map.json',sources)
        store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    except BaseException as exc:
        fatal=type(exc).__name__
        metrics={'errors':1,'questions_completed':len(cases),'model_calls':len(calls)}
        raise
    finally:
        client.chat=original
        store.close()
        recorder.finish(metrics,error=fatal)
    return recorder.path


async def capture_production(workspace, settings, request, *, local_embeddings=False, index_local_chunks=0, embedding_backend="configured"):
    request = QueryRequest.model_validate(request)
    if request.use_query_model or request.budget_unit == "reader_tokens":
        raise ValueError("Capture uses no LLM; use chars/utf8_bytes and disable query model")
    if not local_embeddings and request.mode == "dense":
        raise ValueError("Dense capture requires explicit --local-embeddings")
    if type(index_local_chunks) is not int or not 0<=index_local_chunks<=32 or (index_local_chunks and not local_embeddings):
        raise ValueError("Index only 0..32 chunks with explicit offline local embeddings")
    if embedding_backend not in {"configured","process"} or (embedding_backend=="process" and not local_embeddings):
        raise ValueError("Process override requires explicit local embeddings")
    source = workspace.path("memory", "memoria.db")
    if not source.is_file():
        raise ValueError("Account memory database missing")
    embedder = None
    if local_embeddings:
        import copy
        from ..models_config import apply_account_settings
        settings = copy.deepcopy(settings)
        apply_account_settings(settings, workspace.directory)
        if embedding_backend=="process":
            from ..config import ModelConfig
            name=settings.embedding.model.replace('\\','/').split('/')[-1]
            if name not in {"bge-small-zh-v1.5","bge-small-en-v1.5"} or not (settings.root/"model"/name).is_dir():
                raise ValueError("Process override requires an existing supported local BGE directory")
            settings.embedding=ModelConfig(model=str(settings.root/"model"/name),api_key="",base_url="local://auto")
        embedder = EmbeddingClient(settings.embedding,timeout_seconds=30,max_retries=0)
        from ..memory.experience import local_encoder
        if not local_encoder(embedder):
            raise ValueError("Only configured offline local embeddings are permitted")
    recorder = RunRecorder(workspace.path("experiments"), settings.root, datasets=[],
        models={"embedding_namespace":embedder.namespace if embedder else None, "reader":None},
        protocol={"owner_user_id":workspace.owner, "mode":"production_snapshot_replay",
            "request":request.model_dump(mode="json"), "retrieval_variant":"local_embedding" if embedder else "lexical_only",
            "embedding_backend":embedding_backend,"index_local_chunks":index_local_chunks,
            "reader_called":False, "production_writes":0, "gold_read":False,
            "scope":"same production access code on a frozen account copy; not extraction/QA quality"}, seed=None)
    metrics = {"errors":1, "llm_calls":0, "production_writes":0, "quality":"unscored"}
    fatal = None
    try:
        frozen = recorder.path / "account-snapshot.db"
        readonly = sqlite3.connect(source.resolve().as_uri()+"?mode=ro", uri=True)
        copied = sqlite3.connect(frozen)
        try:
            readonly.backup(copied)
        finally:
            copied.close(); readonly.close()
        # Opening Store may migrate/rebuild indexes: preserve the captured input bytes.
        with tempfile.TemporaryDirectory(prefix="memoria-production-replay-") as temporary:
            working = Path(temporary)/"working.db"
            import shutil
            shutil.copyfile(frozen,working)
            store = Store(working, vector_backend="json")
            try:
                claims = ClaimMemory(store, workspace.owner)
                engine = MemoryEngine(store,embedder)
                if index_local_chunks:
                    from ..memory.experience import reindex_dense
                    metrics["experience_index"] = await reindex_dense(store,embedder,index_local_chunks)
                packet = await MemoryAccessOrchestrator(claims,engine).access(request)
                write_json(recorder.path/"production-capture.json",packet)
                write_json(recorder.path/"selection-replay.json",replay(packet,workspace.owner))
                recorder.append("events.jsonl", {"snapshot_watermark":watermark(store),
                    "historical_event_count":store.db.execute("SELECT COUNT(*) FROM memory_trace_events").fetchone()[0]})
                metrics.update(errors=0,candidate_count=len(packet["candidates"]),
                    presented_count=len(packet["selection"]["payload"]["evidence"]),
                    changed_stages=packet["production_trace"]["changed_stages"],
                    experience_diagnostics=packet["retrieval"].get("experiences"),
                    integrity=store.db.execute("PRAGMA integrity_check").fetchone()[0])
            finally:
                store.close()
    except BaseException as exc:
        fatal=type(exc).__name__
        raise
    finally:
        recorder.finish(metrics,error=fatal)
    return recorder.path


def replay_file(workspace, settings, path, *, budgets=None):
    source = Path(path).resolve(strict=True)
    if not source.is_relative_to(workspace.directory) or source.is_symlink():
        raise ValueError("Capture must be in this account's private directory")
    packet = json.loads(source.read_text(encoding="utf-8"))
    result = replay(packet,workspace.owner,budgets=budgets)
    recorder = RunRecorder(workspace.path("experiments"),settings.root,datasets=[source],models={},
        protocol={"mode":"production_fixed_pool_selection", "owner_user_id":workspace.owner,
                  "model_calls":0,"gold_read":False,"budgets":budgets}, seed=None)
    write_json(recorder.path/"selection-replay.json",result)
    recorder.finish({"errors":0,"model_calls":0,"quality":"unscored","production_writes":0})
    return recorder.path
