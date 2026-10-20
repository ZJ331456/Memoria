"""Typed, source-preserving read contracts shared by applications and the memory kernel."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Domain = Literal['memory', 'conversation', 'library']


class MemorySearch(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    query: str = Field(min_length=1, max_length=2000)
    domains: list[Domain] = Field(default_factory=lambda: ['memory', 'library'], min_length=1, max_length=3)
    session_id: str | None = Field(default=None, min_length=1, max_length=64)
    limit: int = Field(default=8, ge=1, le=16)
    budget_chars: int = Field(default=8000, ge=800, le=16000)
    memory_mode: Literal['keyword', 'bm25', 'dense', 'rrf'] = 'rrf'

    @field_validator('query')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('query cannot be blank')
        return value

    @field_validator('domains')
    @classmethod
    def unique(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('domains must be unique')
        return value


class KnowledgeAnswer(MemorySearch):
    domains: list[Domain]=Field(default_factory=lambda:['library'],min_length=1,max_length=1)

    @field_validator('domains')
    @classmethod
    def source_domain(cls,value):
        if value!=['library']:raise ValueError('Source Reader uses library only; personal state uses QueryRequest, other domains use search')
        return value


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    domain: Domain
    source_id: str
    representation: str
    content: str
    start: int
    end: int
    citation: str
    provenance: dict
    dependency: dict

    def public(self):
        return {key: getattr(self, key) for key in (
            'domain', 'source_id', 'representation', 'content', 'start', 'end', 'citation', 'provenance')}


def excerpt(text: str, query: str, *, start: int | None = None, width: int = 800):
    """An exact bounded slice, never a fabricated quote or a token-concatenated snippet."""
    from .lexical import token_sequence
    if start is None:
        lowered = text.lower()
        position = lowered.find(query.lower())
        if position < 0:
            positions = [lowered.find(t.lower()) for t in token_sequence(query)[:32]]
            position = min((p for p in positions if p >= 0), default=0)
        start = max(0, position - width // 4)
    start = min(max(0, start), max(0, len(text) - width))
    return start, min(len(text), start + width), text[start:start + width]


class SourceReaders:
    """Adapters preserve existing tables, author boundaries and dependency fingerprints."""

    def __init__(self, store, library):
        self.store, self.library = store, library

    def conversation(self, item: dict, query: str = '', *, full=False):
        # Look up persisted origin rather than trusting titles or caller metadata.
        source = self.store.message_source(item.get('id', item.get('message_id')))
        if not source:
            return None
        mid = source['message_id']
        from .experience import personal_source_sql
        with self.store.lock:
            row = self.store.db.execute('SELECT (' + personal_source_sql('m') + ') FROM messages m WHERE m.id=?',
                                       (mid,)).fetchone()
            if row is None:
                return None
            eligible = row[0]
        start, end, content = (0, len(source['content']), source['content']) if full else excerpt(source['content'], query)
        return EvidenceRecord('conversation', mid, 'message_quote', content, start, end,
            f'[message:{mid}:{start}:{end}]',
            {'role': source['role'], 'session_id': source['session_id'], 'origin': source['origin'],
             'personal_evidence_eligible': bool(eligible),
             'recorded_at': source['created_at'], 'fact_time': None}, self.store.message_dependency(source))

    def memory(self, item: dict, query: str = '', *, full=False):
        chunk = item.get('retrieval', {}).get('chunk', {})
        if full:
            start, end, content = 0, len(item['content']), item['content']
        elif chunk:
            base = chunk['start']
            start, end, content = excerpt(item['content'][base:chunk['end']], query)
            start, end = base + start, base + end
        else:
            start, end, content = excerpt(item['content'], query)
        return EvidenceRecord('memory', item['id'], 'derived_memory', content, start, end,
            f"[memory:{item['id']}:{start}:{end}]",
            {'kind': item['kind'], 'source': item.get('source'), 'source_ref': item.get('source_ref'),
             'recorded_at': item.get('created_at'), 'valid_at': item.get('valid_at'),
             'retrieval': {k: v for k, v in item.get('retrieval', {}).items() if k in (
                 'score', 'keyword_rank', 'bm25_rank', 'vector_rank', 'vector_similarity')}},
            self.store.memory_dependency(item))

    @staticmethod
    def library_hit(item: dict):
        if item.get('representation')=='consolidated_memory':
            return EvidenceRecord('library',item['source_id'],'consolidated_memory',item['quote'],0,len(item['quote']),
                item['citation'],{'group':item['group'],'applicability':item['applicability'],
                    'support':item['support'],'independence':item['independence'],'revision_id':item['revision_id']},item['dependency'])
        return EvidenceRecord('library', item['source_id'], 'source_quote', item['quote'], item['start'], item['end'],
            item['citation'], {'kind': item['kind'], 'title': item['title'], 'authorship': item['authorship'],
                'scope': item['scope'], 'revision_id': item['revision_id'], 'sha': item['sha'],
                'knowledge_cards': item['knowledge_cards'], 'file_location': item.get('file_location')}, item['dependency'])
