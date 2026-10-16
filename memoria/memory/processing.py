"""Read persisted extraction/admission outcomes, never run or infer an LLM decision."""
from __future__ import annotations
from .experience import personal_source_sql


def message_origin(store,message_id):
    row=store.db.execute('SELECT channel FROM message_origins WHERE message_id=?',(message_id,)).fetchone()
    return {'channel':row['channel'] if row else 'unknown','basis':'server_ingress' if row else 'not_recorded'}


def message_states(store,ids):
    ids=list(dict.fromkeys(ids))
    if not ids:return {}
    if len(ids)>200:raise ValueError('Message status batch exceeds 200')
    placeholders=','.join('?' for _ in ids)
    rows=store.db.execute('SELECT m.id,m.role,('+personal_source_sql('m')+') eligible,'
        'j.id job_id,j.status,j.extracted_count,j.staged_count,j.attempts,j.updated_at '
        'FROM messages m LEFT JOIN memory_jobs j ON j.source_ref=m.id WHERE m.id IN ('+placeholders+')',ids).fetchall()
    counts={r['source_ref']:dict(r) for r in store.db.execute('''SELECT r.source_ref,COUNT(*) total,
        SUM(r.status='approved') approved,SUM(r.status='rejected') rejected,
        SUM(r.status='applying') applying,SUM(r.status='pending') pending,
        SUM(r.status='pending' AND EXISTS(SELECT 1 FROM memory_review_assessments a WHERE a.review_id=r.id)) assisted
        FROM memory_reviews r WHERE r.source_ref IN ('''+placeholders+') GROUP BY r.source_ref',ids)}
    result={}
    for row in rows:
        reviews=counts.get(row['id'],{})
        number=lambda key:int(reviews.get(key,0) or 0)
        if not row['eligible']:state='not_applicable'
        elif row['status'] in {'pending','running','retry','failed'}:state=row['status']
        elif number('applying'):state='reviewing'
        elif number('pending'):state='needs_assistance' if number('assisted')==number('pending') else 'awaiting_review'
        elif number('approved'):state='stored'
        elif number('rejected'):state='rejected'
        elif row['status']=='completed':state='no_candidates' if row['extracted_count']==0 else 'legacy_unknown'
        else:state='not_queued'
        result[row['id']]={'origin':message_origin(store,row['id']),
            'memory_processing':{'state':state,'job_id':row['job_id'],'job_status':row['status'],
                'extracted_count':row['extracted_count'],'staged_count':row['staged_count'],
                'approved':number('approved'),'pending':number('pending'),'rejected':number('rejected'),
                'attempts':row['attempts'],'updated_at':row['updated_at'],
                'active':state in {'pending','running','retry','reviewing','awaiting_review'}}}
    return result
