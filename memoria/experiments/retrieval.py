"""Prefix-only source retrieval: no gold, future messages or personal assertions."""
from __future__ import annotations

import hashlib
import math

from ..memory.embedding import EmbeddingClient

DENSE_BASELINES = frozenset({"B2_dense", "B2_rrf"})
BASELINES = frozenset({"B0_recent", "B2_fts", "B2_fts_compact", *DENSE_BASELINES})


class PrefixRetriever:
    """Run-local vectors; candidate membership always comes from this exact prefix.

    Encoding a source earlier for another probe never makes it eligible here.
    Document and query encodings follow the configured BGE production protocol.
    No similarity cutoff is tuned against gold; whole messages enter the reader.
    """
    def __init__(self, embedding: EmbeddingClient, *, rrf_k=60, lexical_select=None):
        if type(rrf_k) is not int or rrf_k < 1:
            raise ValueError("Invalid RRF constant")
        self.embedding, self.rrf_k = embedding, rrf_k
        # Historical LCCC runs retain their frozen compact branch by default.
        self.lexical_select = lexical_select
        self.documents = {}
        self.document_encodes = self.query_encodes = self.document_cache_hits = 0

    async def select(self, history, question, baseline, limit, *, timings):
        if baseline not in DENSE_BASELINES or type(limit) is not int or limit < 1:
            raise ValueError("Invalid dense retrieval settings")
        if len({m["id"] for m in history}) != len(history):
            raise ValueError("Duplicate source IDs in visible prefix")
        keys = []
        for item in history:
            digest = hashlib.sha256(item["content"].encode()).hexdigest()
            if item.get("content_sha256", digest) != digest:
                raise ValueError("Visible source SHA differs from original")
            keys.append((self.embedding.namespace, item["id"], digest))
        missing = [(key, item) for key, item in zip(keys, history, strict=True) if key not in self.documents]
        self.document_cache_hits += len(history) - len(missing)
        if missing:
            with timings.measure("retrieval.embedding.documents"):
                encoded = await self.embedding.embed_documents([item["content"] for key,item in missing])
            if len(encoded) != len(missing):
                raise ValueError("Incomplete prefix document encoding")
            # Publish only after validating the complete response; no partial cache.
            prepared = {}
            for (key,item), document in zip(missing,encoded,strict=True):
                chunks = document.chunks
                if not chunks:
                    raise ValueError("Source retrieval requires complete chunk spans")
                cursor = 0
                for chunk in chunks:
                    if chunk["start"] != cursor or not cursor <= chunk["end"] <= len(item["content"]):
                        raise ValueError("Document chunks have a gap or overlap")
                    cursor = chunk["end"]
                if cursor != len(item["content"]):
                    raise ValueError("Incomplete source chunk coverage")
                prepared[key] = chunks
            self.documents.update(prepared)
            self.document_encodes += len(missing)
        if not history:
            return [], {"candidates":[],"prefix_messages":0,"rrf_k":self.rrf_k}
        with timings.measure("retrieval.embedding.query"):
            query = await self.embedding.embed_query(question)
        self.query_encodes += 1
        if not query:
            raise ValueError("Query embedding unavailable")
        with timings.measure("retrieval.ranking"):
            scored = []
            for index,(key,item) in enumerate(zip(keys,history,strict=True)):
                best = None
                for chunk in self.documents[key]:
                    vector = chunk["embedding"]
                    if len(vector)!=len(query):
                        raise ValueError("Query/document vector dimensions differ")
                    dot = sum(a*b for a,b in zip(query,vector,strict=True))
                    norm = math.sqrt(sum(a*a for a in query)*sum(b*b for b in vector))
                    if not norm or not math.isfinite(dot) or not math.isfinite(norm):
                        raise ValueError("Non-finite or zero vector")
                    candidate = (dot/norm,chunk["start"],chunk["end"])
                    if best is None or candidate[0]>best[0]:
                        best = candidate
                scored.append((index,item,best))
            dense = sorted(scored,key=lambda row:(-row[2][0],row[0]))[:limit]
            dense_rank = {item["id"]:rank for rank,(_,item,_) in enumerate(dense,1)}
            lexical_rank = {}
            if baseline=="B2_rrf":
                # Same compact lexical branch as the already frozen baseline.
                from .runner import select_history
                lexical = (self.lexical_select(history, question, limit) if self.lexical_select else
                           select_history(history,question,"B2_fts_compact",limit))
                lexical_rank = {item["id"]:rank for rank,item in enumerate(lexical,1)}
                candidates = [row for row in scored if row[1]["id"] in dense_rank or row[1]["id"] in lexical_rank]
                def fusion(row):
                    mid = row[1]["id"]
                    return sum(1/(self.rrf_k+ranks[mid]) for ranks in (dense_rank,lexical_rank) if mid in ranks)
                ranked = sorted(candidates,key=lambda row:(-fusion(row),row[0]))[:limit]
            else:
                ranked = dense
            diagnostics = [{"message_id":item["id"], "content_sha256":key[2],
                            "dense_score":best[0],"dense_rank":dense_rank.get(item["id"]),
                            "lexical_rank":lexical_rank.get(item["id"]),
                            "rrf_score":fusion((index,item,best)) if baseline=="B2_rrf" else None,
                            "best_chunk":{"start":best[1],"end":best[2]}}
                           for index,item,best in ranked for key in [keys[index]]]
        return [item for _,item,_ in ranked], {"candidates":diagnostics,"prefix_messages":len(history),
                "document_encodes_total":self.document_encodes,"document_cache_hits_total":self.document_cache_hits,
                "query_encodes_total":self.query_encodes,"rrf_k":self.rrf_k,
                "future_candidates":0,"budget_unit":"whole original messages"}
