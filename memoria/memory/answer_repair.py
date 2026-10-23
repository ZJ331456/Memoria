"""One deletion-only answer repair, using already verified literal answer units."""
import re

from .answer_risk import normalized


INSTRUCTION = (
    '\nThis is the ONLY minimal-answer repair. Use the SAME presented original evidence and question. '
    'Select only entries in retained_answer_units that still answer EVERY requested requirement. '
    'Copy selected entries literally, in their original order, separated only by sentence punctuation or whitespace. '
    'Do not shorten within an entry, add words, add explanations, change a subject/condition/date/negation, '
    'or use a summary to introduce facts. If the retained entries do not answer the whole question, abstain. '
    'Keep ALL required_evidence_ids, including identity, condition and time dependencies. '
    'Return the same strict status/answer/evidence_ids JSON schema. A second independent support verification '
    'will inspect the COMPLETE repaired answer against the SAME original evidence.')


def retained_units(check, original_answer):
    """Only independently supported units passing applicable program guards survive.

    Global subject/high-impact/absolute blockers cannot be safely assigned to one
    unit, so those answers are ineligible for this local repair.
    """
    local_codes={b['code'] for b in check.get('claim_rule_blockers', [])}
    if set(check.get('rule_blockers', [])) - local_codes or check.get('verdict') == 'contradicted':
        return []
    allow_inference = check['query_risk']['allows_background_inference']
    claims = check['claims']
    blocked = {b['claim_index'] for b in check.get('claim_rule_blockers', [])}
    retained = []
    for index, claim in enumerate(claims):
        if index in blocked or claim['relation'] != 'supported' or (claim['basis'] == 'inference' and not allow_inference):
            continue
        # A clock-only unit cannot retain a dependency whose event was dropped.
        if any(d['claim_index'] == index and any(i in blocked or claims[i]['relation'] != 'supported' or
                (claims[i]['basis'] == 'inference' and not allow_inference)
                for i in d['event_claim_indices']) for d in check.get('support_dependencies', [])):
            continue
        retained.append(claim['statement'])
    return sorted(retained, key=lambda value: normalized(original_answer).find(normalized(value)))


def validate_deletion(answer, units, required_ids):
    """No new token or partial unit can be smuggled through an answer repair."""
    if answer.status != 'answer':
        return
    if not set(required_ids) <= set(answer.evidence_ids):
        raise ValueError('Minimal answer repair omitted necessary citations')
    # Eight units at most; match a subsequence of whole literal units. Punctuation
    # is permitted only BETWEEN units, never as a replacement for a unit's words.
    text = normalized(answer.answer).strip()
    separators = r'[\s.,;:!?，、。；：！？]*'
    states = {0}
    for unit in units:
        next_states = set(states)
        literal = normalized(unit)
        for cursor in states:
            start = re.match(separators, text[cursor:]).end() + cursor
            if text.startswith(literal, start):
                next_states.add(start + len(literal))
        states = next_states
    if not any(cursor and re.fullmatch(separators, text[cursor:]) for cursor in states):
        raise ValueError('Minimal answer repair introduced or altered answer content')
