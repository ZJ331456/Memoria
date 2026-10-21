"""Filter reported EVENT evidence, never manufacture an enduring personal state."""
from dataclasses import replace

from ..store import normalize_time
from . import observation_semantics as semantics


def filter_records(records,request):
    if not any((request.event_start,request.event_end,request.recorded_as_of,request.subject_ref)):return records,[]
    retained,rejected=[],[]
    for record in records:
        cutoff=request.recorded_as_of
        recorded=record.provenance.get('source_clock',{}).get('recorded_at',record.provenance.get('recorded_at'))
        updated=record.dependency.get('updated_at',recorded)
        if cutoff and (not recorded or normalize_time(recorded)>normalize_time(cutoff) or (updated and normalize_time(updated)>normalize_time(cutoff))):
            rejected.append({'id':record.source_id,'reason':'source_or_version_recorded_after_cutoff'});continue
        rows=[o for o in record.provenance.get('observations',[]) if semantics.matches(o,
            event_start=request.event_start,event_end=request.event_end,recorded_as_of=cutoff,subject_ref=request.subject_ref)]
        if not rows:
            rejected.append({'id':record.source_id,'reason':'no_verified_observation_matching_event_subject_or_recorded_time'});continue
        if record.representation=='derived_memory':
            retained.append(replace(record,provenance={**record.provenance,'observations':rows}));continue
        for row in rows:
            a=min(e['start'] for e in row['evidence_ref']);b=max(e['end'] for e in row['evidence_ref'])
            if a<record.start or b>record.end:continue
            dependency={**record.dependency};provenance={**record.provenance,'observations':[row]}
            if record.domain=='library':
                dependency['card_ids']=[];provenance['knowledge_cards']=[]
                citation=f"[library:{provenance['revision_id']}:{a}:{b}]"
            else:citation=f'[message:{record.source_id}:{a}:{b}]'
            retained.append(replace(record,start=a,end=b,content=record.content[a-record.start:b-record.start],
                citation=citation,dependency=dependency,provenance=provenance))
    return retained,rejected


def references(rows,domain):
    from .source_support import digest
    import json
    return [{'domain':domain,'observation_id':r['observation_id'],
        'sha256':digest(json.dumps({k:v for k,v in r.items() if k!='observation_id'},sort_keys=True,ensure_ascii=False))} for r in rows]


def valid(store,refs,library=None):
    import json
    from .source_support import digest
    from .formation_store import live_observation
    if not isinstance(refs,list) or len(refs)>32:return False
    for ref in refs:
        if not isinstance(ref,dict) or set(ref)!={'domain','observation_id','sha256'}:return False
        if any(not isinstance(ref[k],str) for k in ref):return False
        domain,oid=ref['domain'],ref['observation_id'];data=semantics.load(store,domain,oid)
        if not data or digest(json.dumps(semantics.projection(data),sort_keys=True,ensure_ascii=False))!=ref['sha256']:return False
        if domain=='conversation':
            row=store.db.execute('SELECT * FROM memory_observations WHERE id=?',(oid,)).fetchone()
            try:live_observation(store,row,allow_archived=True)
            except ValueError:return False
        elif domain=='library':
            if not library or not library.formation._live(library.formation._observation(oid),complete=False):return False
        else:return False
    return True
