"""Immutable, source-local attribution for imported text transcripts.

An attributed utterance is a source assertion, never verified identity or a user Claim.
The header and body are both literal parts of the revision's hashed content.
"""
from __future__ import annotations

import json
from pydantic import BaseModel, ConfigDict, Field


class SourceUnit(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(min_length=1, max_length=160)
    speaker: str = Field(min_length=1, max_length=80)
    session_date_raw: str = Field(default='', max_length=120)
    start: int = Field(ge=0)
    body_start: int = Field(ge=0)
    end: int = Field(ge=1)

    def header(self):
        return json.dumps({'source_unit': self.id, 'speaker': self.speaker,
                           'session_date_raw': self.session_date_raw}, ensure_ascii=False,
                          sort_keys=True, separators=(',', ':')) + '\n'


def render_turns(turns):
    """Adapters preserve raw utterances; no escaping inside the evidence body."""
    pieces, units, cursor = [], [], 0
    for turn in turns:
        body = turn['text']
        unit = SourceUnit(id=turn['id'], speaker=turn.get('speaker') or 'unknown',
                          session_date_raw=turn.get('session_date_raw') or '',
                          start=cursor, body_start=cursor, end=cursor+1)
        header = unit.header()
        unit.body_start = cursor + len(header)
        unit.end = unit.body_start + len(body)
        pieces.append(header + body)
        units.append(unit.model_dump())
        cursor = unit.end + 1
    return '\n'.join(pieces), units


def validate_units(content, units):
    ids, previous = set(), 0
    for unit in units:
        if unit.id in ids or not previous <= unit.start < unit.body_start < unit.end <= len(content):
            raise ValueError('Invalid, overlapping or duplicate source units')
        if content[unit.start:unit.body_start] != unit.header():
            raise ValueError('Source unit attribution must match its literal header')
        if content[previous:unit.start].strip():
            raise ValueError('Source units cannot hide unindexed source text')
        ids.add(unit.id)
        previous = unit.end
    if units and content[previous:].strip():
        raise ValueError('Source units cannot omit a trailing source passage')


def install(store):
    store.db.execute('''CREATE TABLE IF NOT EXISTS library_source_units(
        revision_id TEXT PRIMARY KEY REFERENCES library_revisions(id) ON DELETE CASCADE,
        units_json TEXT NOT NULL)''')
    store.db.execute('''CREATE TRIGGER IF NOT EXISTS library_source_units_immutable
        BEFORE UPDATE ON library_source_units BEGIN
        SELECT RAISE(ABORT,'source units are immutable'); END''')


def save(store, revision_id, content, units):
    validate_units(content, units)
    if units:
        store.db.execute('INSERT INTO library_source_units VALUES(?,?)',
                         (revision_id, json.dumps([u.model_dump() for u in units], ensure_ascii=False)))


def load(store, revision):
    row = store.db.execute('SELECT units_json FROM library_source_units WHERE revision_id=?',
                           (revision['id'],)).fetchone()
    units = [SourceUnit.model_validate(u) for u in json.loads(row[0])] if row else []
    validate_units(revision['content'], units)
    return units


def containing(store, revision, start, end):
    return next((u for u in load(store, revision) if u.body_start <= start < end <= u.end), None)
