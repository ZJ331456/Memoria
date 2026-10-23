"""Bounded source-domain retrieval. Vectors are a disposable, versioned hint.

Each result remains an original passage, never a generated memory or personal fact.
Local BGE is indexed lazily; remote indexing is explicit to avoid an unmetered
bulk request on the first question. No gold, model answer or future source is read.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import hashlib
import json
import math
import time
from functools import lru_cache

from .embedding import EmbeddingError
from .lexical import token_sequence
from .source_units import SourceUnit, validate_units

POLICY = "source-passages-rrf-v2-english-morphology"
SCAN_LIMIT = 2000
TEXT_LIMIT = 1600000
WEIGHTS = {"keyword": 0.8, "bm25": 0.9, "dense": 1.0}


def terms(text):
    # Query function words carry no entity, event or polarity. Do not discard
    # not/never, names, numbers or conditions. This does not mutate source text.
    stop = {"a", "an", "and", "are", "as", "at", "be", "been", "did", "do", "does",
            "for", "from", "had", "has", "have", "how", "in", "is", "it", "of", "on",
            "or", "that", "their", "them", "there", "these", "they", "this", "to", "was",
            "were", "which", "who", "with", "would", "he", "she", "him", "her", "his",
            "my", "me", "our", "ours", "we", "you", "its"}
    return [stem(t) for t in token_sequence(text) if t not in stop]


@lru_cache(maxsize=8192)
def stem(token):
    # Retrieval morphology only; original quotes and the identity safety catalog
    # never use these stems as entity names, aliases or factual evidence.
    if token.isascii() and token.isalpha():
        return english_stemmer().stem(token)
    return token


@lru_cache(maxsize=1)
def english_stemmer():
    from nltk.stem import PorterStemmer
    return PorterStemmer()


def vector(value, dimension=None):
    if (not isinstance(value, list) or not value or any(type(v) not in (int, float)
            or not math.isfinite(v) for v in value) or not any(value)
            or dimension is not None and len(value) != dimension):
        raise EmbeddingError("Invalid source vector or dimension")
    return value


def cosine(a, b):
    vector(b, len(a))
    return sum(x*y for x, y in zip(a, b, strict=True)) / (
        math.sqrt(sum(x*x for x in a))*math.sqrt(sum(x*x for x in b)))


class SourceRetrieval:
    def __init__(self, library, embedder):
        self.library, self.store, self.embedder = library, library.store, embedder
        self._index_lock = asyncio.Lock()
        with self.store.memory_transaction():
            self.store.db.execute('''CREATE TABLE IF NOT EXISTS library_vectors(
                chunk_id INTEGER NOT NULL REFERENCES library_chunks(id) ON DELETE CASCADE,
                namespace TEXT NOT NULL,document_sha TEXT NOT NULL,dimension INTEGER NOT NULL,
                vectors_json TEXT NOT NULL,PRIMARY KEY(chunk_id,namespace))''')
            self.store.db.execute('''CREATE TRIGGER IF NOT EXISTS library_vectors_stale
                AFTER UPDATE OF head_id,status ON library_sources BEGIN
                DELETE FROM library_vectors WHERE chunk_id IN (SELECT c.id FROM library_chunks c
                JOIN library_revisions r ON r.id=c.revision_id WHERE r.source_id=NEW.id
                AND (r.id!=NEW.head_id OR NEW.status!='active')); END''')

    def snapshot(self, session_id):
        """Apply source/session/head filters before the bounded scan, not after top-k."""
        with self.store.lock:
            rows = self.store.db.execute('''SELECT c.*,s.id AS source_id,s.kind,s.authorship,s.scope,
                r.title,r.sha,r.content,u.units_json FROM library_chunks c
                JOIN library_revisions r ON r.id=c.revision_id JOIN library_sources s ON s.id=r.source_id
                LEFT JOIN library_source_units u ON u.revision_id=r.id
                WHERE s.status='active' AND s.head_id=r.id AND (s.scope='personal' OR s.session_id=?)
                ORDER BY c.id DESC LIMIT ?''', (session_id, SCAN_LIMIT+1)).fetchall()
            result, revisions, used = [], {}, 0
            truncated = len(rows) > SCAN_LIMIT
            for raw in rows[:SCAN_LIMIT]:
                row = dict(raw)
                if row['revision_id'] not in revisions:
                    valid = hashlib.sha256(row['content'].encode()).hexdigest() == row['sha']
                    units = [SourceUnit.model_validate(u) for u in json.loads(row['units_json'] or '[]')]
                    validate_units(row['content'], units)
                    revisions[row['revision_id']] = (valid, units)
                valid, units = revisions[row['revision_id']]
                if not valid or row['content'][row['start']:row['end']] != row['quote']:
                    continue
                unit = next((u for u in units if (u.start, u.end) == (row['start'], row['end'])), None)
                text = (unit.speaker+': '+row['content'][unit.body_start:unit.end]
                        if unit else row['quote'])
                if used+len(text) > TEXT_LIMIT:
                    truncated = True
                    continue
                used += len(text)
                row.pop('content'); row.pop('units_json')
                row['unit'] = unit
                row['embedding_text'] = text
                row['document_sha'] = hashlib.sha256((POLICY+'\n'+text).encode()).hexdigest()
                result.append(row)
            return result, truncated

    def cached(self, rows, namespace):
        if not rows:return {}
        # Read only the already scoped, bounded snapshot, never another session's
        # private vector blobs merely to discard them in Python afterwards.
        marks = ','.join('?' for _ in rows)
        with self.store.lock:
            cache = {r['chunk_id']: dict(r) for r in self.store.db.execute(
                f'SELECT * FROM library_vectors WHERE namespace=? AND chunk_id IN ({marks})',
                (namespace, *[r['id'] for r in rows]))}
        return {r['id']: json.loads(cache[r['id']]['vectors_json']) for r in rows
                if r['id'] in cache and cache[r['id']]['document_sha'] == r['document_sha']}

    async def index(self, rows, embedder, namespace, session_id):
        async with self._index_lock:
            cached = self.cached(rows, namespace)
            missing = [r for r in rows if r['id'] not in cached]
            encoded = []
            dimension = None
            for offset in range(0, len(missing), 32):
                batch = missing[offset:offset+32]
                documents = await embedder.embed_documents([r['embedding_text'] for r in batch])
                if len(documents) != len(batch):
                    raise EmbeddingError('Incomplete source document batch')
                for row, document in zip(batch, documents, strict=True):
                    chunks = document.chunks or [{'start': 0, 'end': len(row['embedding_text']),
                                                  'embedding': document.vector}]
                    if not chunks:
                        raise EmbeddingError('Empty source document chunks')
                    last_end = 0
                    for chunk in chunks:
                        v = vector(chunk['embedding'], dimension)
                        dimension = len(v)
                        if (type(chunk['start']) is not int or type(chunk['end']) is not int
                                or not 0 <= chunk['start'] < chunk['end'] <= len(row['embedding_text'])
                                or chunk['start'] > last_end):
                            raise EmbeddingError('Incomplete source chunk coverage')
                        last_end = chunk['end']
                    if last_end != len(row['embedding_text']):
                        raise EmbeddingError('Truncated source document')
                    encoded.append((row, chunks))
            # No partial batch commit if any model response was malformed. Recheck
            # the actual configured model AND every source lease after awaits.
            if self.embedder() is not embedder or embedder.namespace != namespace:
                raise EmbeddingError('Source embedding model changed during indexing')
            with self.store.memory_transaction():
                existing = {r[0] for r in self.store.db.execute(
                    'SELECT DISTINCT dimension FROM library_vectors WHERE namespace=?', (namespace,))}
                if existing and dimension is not None and existing != {dimension}:
                    raise EmbeddingError('Source embedding namespace dimension changed')
                written = 0
                for row, chunks in encoded:
                    if not self.live(row, session_id):
                        continue
                    self.store.db.execute('INSERT OR REPLACE INTO library_vectors VALUES(?,?,?,?,?)',
                        (row['id'], namespace, row['document_sha'], dimension, json.dumps(chunks)))
                    written += 1
                return written

    def live(self, row, session_id):
        dep = {'dependency_type': 'library', 'id': row['source_id'], 'revision_id': row['revision_id'],
               'sha': row['sha'], 'feedback_id': self.feedback_id(row['revision_id'])}
        return self.library.dependency_valid_for_session(dep, session_id=session_id)

    def feedback_id(self, revision_id):
        feedback = self.library._feedback(revision_id)
        return feedback['id'] if feedback else None

    def hit(self, row, retrieval, query, *, observations=(), include_formed_memory=True):
        rid = row['revision_id']
        cards=self.library.passage_cards(rid,query) if include_formed_memory and not row['unit'] else []
        result = {k: row[k] for k in ('source_id','revision_id','sha','title','kind','authorship',
            'scope','start','end','quote')} | {
            'knowledge_cards': cards, 'file_location': self.library.files.locations(rid,row['start'],row['end']),
            'citation': f"[library:{rid}:{row['start']}:{row['end']}]", 'retrieval': retrieval,
            'dependency': {'dependency_type':'library','id':row['source_id'],'revision_id':rid,
                           'sha':row['sha'],'feedback_id':self.feedback_id(rid)}}
        if cards:result['dependency']['card_ids']=[c['id'] for c in cards]
        if observations:
            from .formed_retrieval import passage_organization
            organization, refs = passage_organization(row, observations, self.library.formation_reference)
            if organization:
                result['formed_memory'] = organization
                result['dependency']['formation_refs'] = refs
        return result

    async def search(self, query, *, session_id=None, limit=12, mode='rrf', expand=False,
                     include_formed_memory=True):
        started = time.perf_counter()
        rows, truncated = self.snapshot(session_id)
        by_id = {r['id']: r for r in rows}
        docs = {r['id']: Counter(terms(r['embedding_text']+
                ('\n'+r['title'] if not r['unit'] and r['start']==0 else ''))) for r in rows}
        qt = set(terms(query)); n = len(rows)
        df = Counter(t for doc in docs.values() for t in doc)
        avg = sum(sum(doc.values()) for doc in docs.values())/max(1,n)
        keyword = {mid:len(qt & doc.keys())/max(1,len(qt)) for mid,doc in docs.items()}
        bm25 = {}
        for mid, doc in docs.items():
            length = sum(doc.values())
            bm25[mid] = sum(math.log(1+(n-df[t]+0.5)/(df[t]+0.5))*doc[t]*2.5 /
                (doc[t]+1.5*(0.25+0.75*length/max(1,avg))) for t in qt if doc[t])
        scores = {'keyword':keyword,'bm25':bm25,'dense':{}}
        diagnostic = {'mode':mode,'policy':POLICY,'rrf_constant':60,'weights':WEIGHTS,
            'scanned_passages':n,'scan_truncated':truncated,'embedding_attempted':False,
            'dense_available':False,'indexed_passages':0,'index_written':0,'fallback':False}
        observations, formed_diagnostic = (self.library.retrieval_observations(
            [row['revision_id'] for row in rows], session_id=session_id)
            if include_formed_memory else ([], {}))
        diagnostic['formed_memory_enabled'] = include_formed_memory
        diagnostic.update(formed_diagnostic)
        embedder = self.embedder()
        if rows and mode in {'dense','rrf'} and embedder and embedder.enabled:
            diagnostic['embedding_attempted'] = True
            try:
                namespace = embedder.namespace
                diagnostic['namespace'] = namespace
                if embedder.is_local:
                    diagnostic['index_written'] = await self.index(rows,embedder,namespace,session_id)
                cached = self.cached(rows,namespace)
                diagnostic['indexed_passages'] = len(cached)
                diagnostic['unindexed_passages'] = n-len(cached)
                if not embedder.is_local and len(cached) < n:
                    diagnostic['index_note'] = 'remote_bulk_index_requires_explicit_bounded_request'
                if cached:
                    qv = vector(await embedder.embed_query(query))
                    if self.embedder() is not embedder or embedder.namespace != namespace:
                        raise EmbeddingError('Source model changed during retrieval')
                    for mid, chunks in cached.items():
                        scores['dense'][mid] = max(cosine(qv,c['embedding']) for c in chunks)
                    diagnostic['dense_available'] = True
            except (EmbeddingError, RuntimeError, ValueError, OSError, TimeoutError) as exc:
                scores['dense'] = {}
                diagnostic.update(dense_available=False,error_type=type(exc).__name__)
        diagnostic['fallback'] = mode == 'rrf' and not diagnostic['dense_available']
        active = list(WEIGHTS) if mode == 'rrf' else [mode]
        lanes = {name: sorted((mid for mid,score in values.items() if score > 0),
                    key=lambda mid:(-values[mid],mid)) for name,values in scores.items()}
        # Formation participates in recall by routing descriptions to raw source
        # units. Its own JSON IDs are never tokenized as content or entity names.
        # Recheck after embedding awaits: stale review/feedback cannot steer a read.
        with self.store.lock:
            observations = [o for o in observations if self.library.retrieval_observation_valid(o)]
            if include_formed_memory:
                diagnostic['live_observations'] = len(observations)
                from .formed_retrieval import rank_observations, routes
                ranked = rank_observations(observations, qt, terms)
                lanes['observation'] = routes(rows, ranked)
                consolidated = self.library.formation.search(query, session_id=session_id, limit=4)
                # Preserve all support chunks as recall alternatives, while search
                # also retains the complete consolidation as an atomic candidate.
                supports = [{**s, 'subject_start': None, 'subject_end': None, 'subject_quote': ''}
                            for memory in consolidated for s in memory['support']]
                lanes['consolidation'] = routes(rows, supports)
                active += ['observation', 'consolidation']
                diagnostic.update(matched_observations=len(ranked),
                    observation_hint_passages=len(lanes['observation']),
                    consolidation_hint_passages=len(lanes['consolidation']),
                    valid_consolidations=len(consolidated))
        weights = {**WEIGHTS, 'observation': 0.9, 'consolidation': 0.9}
        diagnostic['weights'] = {name: weights[name] for name in active}
        diagnostic['formation_policy'] = 'reviewed-description-to-original-v1'
        fused = Counter()
        for name in active:
            for rank,mid in enumerate(lanes[name],1):
                fused[mid] += weights[name]/(60+rank)
        ordered = sorted(fused,key=lambda mid:(-fused[mid],mid))
        with self.store.lock:
            ordered = [mid for mid in ordered if self.live(by_id[mid],session_id)]
            chosen = ordered[:limit]
            # Adjacency is a retrieval hint, NOT an asserted identity or causal link.
            # Keep separate citations for each raw turn. The gate chooses and quotes
            # any actually necessary bridge; proximity alone never approves a fact.
            edges = []
            if expand:
                groups = {}
                for row in rows:
                    if row['unit']:
                        groups.setdefault(row['revision_id'],[]).append(row)
                for group in groups.values():group.sort(key=lambda r:r['start'])
                for mid in chosen[:4]:
                    row = by_id[mid]; group = groups.get(row['revision_id'],[])
                    position = next((i for i,r in enumerate(group) if r['id']==mid),None)
                    if position is None:continue
                    for i in (position-1,position+1):
                        if not 0 <= i < len(group):continue
                        other = group[i]['id']
                        if len(chosen) < min(16,limit+4) and other not in chosen and self.live(group[i],session_id):
                            chosen.append(other)
                            edges.append({'from':mid,'to':other,'kind':'source_adjacency_hint',
                                          'semantic_link_verified':False})
            hits = []
            for mid in chosen:
                retrieval = {'score':fused[mid], 'lane_ranks':{name:lane.index(mid)+1 for name,lane in lanes.items() if mid in lane}}
                hits.append(self.hit(by_id[mid],retrieval,query,observations=observations,
                                     include_formed_memory=include_formed_memory))
            diagnostic['lanes'] = {name:[f"[library:{by_id[mid]['revision_id']}:{by_id[mid]['start']}:{by_id[mid]['end']}]"
                for mid in lane[:32]] for name,lane in lanes.items()}
            diagnostic['adjacency_hints'] = edges
            diagnostic['elapsed_ms'] = round((time.perf_counter()-started)*1000,3)
            return hits,diagnostic
