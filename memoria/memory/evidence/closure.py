"""Select necessary source/identity/support units atomically, without gold labels."""
from .budget import EvidenceBudget


def select_closed(candidates, decision, citation_owners, budget: EvidenceBudget, *, limit=8):
    catalog = {c['id']: c for c in candidates}
    if len(catalog) != len(candidates):
        raise ValueError('Duplicate closure candidate IDs')
    necessary = set()
    groups = []
    for requirement in decision['requirements']:
        owners = set()
        for source in requirement['sources']:
            if source['id'] not in citation_owners:
                raise ValueError('Unpresented dependency')
            owners.update(citation_owners[source['id']])
        groups.append({'requirement': requirement['fact'], 'candidate_ids': sorted(owners),
                       'status': requirement['status'],
                       'quote_nodes': [{'citation':s['id'],'field':s.get('field','text')}
                                       for s in requirement['sources']],
                       'link_basis':'co_required_by_frozen_requirement',
                       'semantic_link_verified':False})
        necessary.update(owners)
    pending = list(necessary)
    while pending:
        mid = pending.pop()
        if mid not in catalog:
            raise ValueError('Unresolved evidence dependency')
        for dependency in catalog[mid].get('requires', []):
            if dependency not in necessary:
                necessary.add(dependency)
                pending.append(dependency)
    chosen = [c for c in candidates if c['id'] in necessary]
    facets = [r['fact'] for r in decision['requirements']]
    payload = {'evidence': chosen, 'requirements': facets}
    fits = len(chosen) <= limit and budget.cost(payload) <= budget.limit
    # Never publish a partial necessary group just because its first member fits.
    if not fits:
        payload = {'evidence': [], 'requirements': []}
    return {'payload': payload, 'strategy': 'dependency_closed_requirements', 'cost': budget.cost(payload),
        'budget': budget.public(), 'candidate_ids': list(catalog), 'groups': groups,
        'covered': facets if fits and decision['state'] == 'sufficient' else [],
        'uncovered': [] if fits and decision['state'] == 'sufficient' else facets,
        'complete': fits and decision['state'] == 'sufficient', 'fits': fits,
        'trace': [{'id': c['id'], 'decision': 'selected' if fits and c['id'] in necessary else
                   'necessary_group_over_budget' if c['id'] in necessary else 'not_required'} for c in candidates],
        'semantic_sufficiency_verified': False}
