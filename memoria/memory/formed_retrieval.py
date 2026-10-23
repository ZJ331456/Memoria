"""Reviewed-memory routes to original passages; no answer labels or new facts."""
from collections import Counter
import json
import math


SUMMARY_BUDGET = 1200


def rank_observations(observations, query_terms, terms):
    documents = [Counter(terms(o['retrieval_text'])) for o in observations]
    counts = Counter(t for document in documents for t in document)
    n = len(documents)
    average = sum(sum(d.values()) for d in documents) / max(n, 1)
    scores = []
    for observation, document in zip(observations, documents, strict=True):
        length = sum(document.values())
        score = sum(math.log(1 + (n-counts[t]+0.5)/(counts[t]+0.5)) * document[t]*2.5 /
                    (document[t]+1.5*(0.25+0.75*length/max(1, average)))
                    for t in query_terms if document[t])
        if score > 0:
            scores.append((score, observation))
    return [o for _, o in sorted(scores, key=lambda pair: (-pair[0], pair[1]['id']))]


def routes(rows, observations):
    """One rank vote per original passage, regardless of repeated summaries.

    A route retains every raw chunk overlapping the observation and its subject
    proof. Neither proximity nor sharing a topic asserts an entity/event link.
    """
    by_revision = {}
    for row in rows:
        by_revision.setdefault(row['revision_id'], []).append(row)
    result, seen = [], set()
    for observation in observations:
        spans = [(observation['start'], observation['end'])]
        if observation['subject_quote']:
            spans.append((observation['subject_start'], observation['subject_end']))
        for row in sorted(by_revision.get(observation['revision_id'], []), key=lambda r: r['start']):
            if row['id'] not in seen and any(a < row['end'] and row['start'] < b for a, b in spans):
                seen.add(row['id'])
                result.append(row['id'])
    return result


def passage_organization(row, observations, reference):
    """Stable, query-independent metadata that cannot crowd out raw evidence.

    Summaries are kept whole with all their applicability text. If one is too
    large, retain its ID as a routing receipt, not a misleading truncated claim.
    Exact quotes remain solely in the original source body/support fields.
    """
    members = sorted((o for o in observations if o['revision_id'] == row['revision_id']
        and o['start'] < row['end'] and row['start'] < o['end']), key=lambda o: (o['start'], o['id']))
    if not members:
        return {}, []
    organization = {'role': 'retrieval_organization_only', 'observations': [],
                    'omitted_summary_count': 0}
    refs = []
    for observation in members:
        if len(refs) >= 4:
            organization['omitted_summary_count'] += 1
            continue
        group = observation['group']
        entry = {'observation_id': observation['id'], 'summary': observation['content'],
                 'applicability': observation['applicability'], 'subject': group.get('subject', ''),
                 'conditions': group.get('conditions', '')}
        future = {**organization, 'observations': [*organization['observations'], entry]}
        if len(json.dumps(future, ensure_ascii=False, separators=(',', ':'))) > SUMMARY_BUDGET - 32:
            # Keep the source readable even when the reviewed summary is larger
            # than its raw evidence. Never silently slice conditions/negations.
            entry = {'observation_id': observation['id'], 'summary_omitted': 'organization_budget'}
            future = {**organization, 'observations': [*organization['observations'], entry]}
            if len(json.dumps(future, ensure_ascii=False, separators=(',', ':'))) > SUMMARY_BUDGET - 32:
                organization['omitted_summary_count'] += 1
                continue
        organization['observations'].append(entry)
        refs.append(reference(observation))
    return organization, refs


def compact_support(support, library):
    """Remove storage-only duplication, retaining the entire necessary group."""
    result = []
    for original in support:
        entry = {key: original[key] for key in (
            'observation_id', 'source_id', 'revision_id', 'start', 'end',
            'quote', 'citation', 'subject_quote', 'subject_citation',
            'feedback_quote', 'reported_outcome', 'applicability')}
        # The same subject words remain verbatim in quote; do not repeat them.
        if entry['subject_quote'] and entry['subject_quote'] in entry['quote']:
            entry['subject_quote'] = ''
            entry['subject_evidence_in_quote'] = True
        from .source_units import containing
        unit = containing(library.store, library._revision(original['revision_id']),
                          original['start'], original['end'])
        if unit:
            entry['source_unit'] = {'id': unit.id, 'speaker': unit.speaker, 'session_date_raw': unit.session_date_raw,
                                    'attribution': 'source_declared', 'identity_verified': False}
        result.append(entry)
    return result
