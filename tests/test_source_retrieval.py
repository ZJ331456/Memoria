"""Temporary engineering data, never source users or research quality scores."""
import asyncio

import pytest

from memoria.experiments.retrieval import PrefixRetriever
from memoria.memory.embedding import DocumentEmbedding
from memoria.observability.timing import StageTimings


class Encoder:
    namespace = "engineering-vectors"
    def __init__(self):
        self.seen = []
    async def embed_documents(self,texts):
        self.seen.extend(texts)
        return [DocumentEmbedding([1.,0.],[{"start":0,"end":len(text),"embedding":[1.,0.],"tokens":len(text)}]) for text in texts]
    async def embed_query(self,text):
        return [1.,0.]


def run(selector,history,question="工程查询",baseline="B2_dense",limit=8):
    return asyncio.run(selector.select(history,question,baseline,limit,timings=StageTimings()))


def test_cached_future_source_never_enters_earlier_prefix():
    encoder = Encoder()
    selector = PrefixRetriever(encoder)
    first = {"id":"first","content":"工程原文"}
    future = {"id":"future","content":"未来工程原文"}
    run(selector,[first,future])
    items,info = run(selector,[first])
    assert items==[first] and info["prefix_messages"]==1
    assert len(encoder.seen)==2 and info["document_cache_hits_total"]==1
    assert info["future_candidates"]==0


def test_rrf_branches_keep_unique_original_sources_and_stable_ranks():
    original = {"id":"lexical","content":"甲 乙 丙 工 程 原 文"}
    earlier = {"id":"earlier","content":"完全不同的临时工程输入"}
    items,info = run(PrefixRetriever(Encoder()),[earlier,original],question="甲乙丙",baseline="B2_rrf",limit=2)
    assert items==[original,earlier] and items[0] is original
    assert info["candidates"][0]["lexical_rank"]==1
    assert info["candidates"][0]["dense_rank"]==2
    assert len({item["id"] for item in items})==2
    assert original["content"]=="甲 乙 丙 工 程 原 文"


def test_document_cache_namespace_and_source_digest_are_bound():
    encoder = Encoder()
    selector = PrefixRetriever(encoder)
    run(selector,[{"id":"same","content":"工程旧文"}])
    run(selector,[{"id":"same","content":"工程新文"}])
    encoder.namespace = "engineering-new-space"
    run(selector,[{"id":"same","content":"工程新文"}])
    assert len(encoder.seen)==3


@pytest.mark.parametrize("change",["duplicate","hash","gap","dimension","nan"])
def test_corrupt_prefix_or_encoding_fails_without_partial_results(change):
    encoder = Encoder()
    selector = PrefixRetriever(encoder)
    history = [{"id":"one","content":"工程原文"}]
    if change=="duplicate":history*=2
    elif change=="hash":history[0]["content_sha256"]="0"*64
    elif change=="gap":
        async def documents(texts):
            return [DocumentEmbedding([1.,0.],[{"start":1,"end":len(texts[0]),"embedding":[1.,0.]}])]
        encoder.embed_documents = documents
    elif change=="dimension":
        async def query(text):return [1.,0.,0.]
        encoder.embed_query = query
    else:
        async def query(text):return [float("nan"),0.]
        encoder.embed_query = query
    with pytest.raises(ValueError):run(selector,history)
    if change in {"duplicate","hash","gap"}:assert selector.documents=={}
