"""Offline synthetic smoke check of native library Keyword/BM25/Dense/RRF.

Creates a temporary SQLite database, uses real local BGE, makes zero API calls.
"""
import argparse
import asyncio
import json
from pathlib import Path
import tempfile
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from memoria.config import Settings, ModelConfig
from memoria.memory.kernel import MemoryKernel
from memoria.memory.library import SourceInput
from memoria.memory.records import MemorySearch
from memoria.store import Store


async def check(model):
    from memoria.experiments.simulation import core_fingerprint
    root=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='memoria-library-bge-') as directory:
        settings=Settings.load(root/'config/system.example.toml')
        settings.database=Path(directory)/'synthetic.db';settings.markdown_enabled=False
        settings.embedding=ModelConfig(str(model.resolve()),'', 'local://auto',backend='bge')
        store=Store(settings.database,'json')
        try:
            kernel=MemoryKernel(settings,store)
            target=kernel.ingest(SourceInput(kind='document',title='Synthetic Atlas incident',
                content='The Atlas storage deployment failed because its database schema was incompatible.',
                authorship='external'))
            kernel.ingest(SourceInput(kind='document',title='Synthetic unrelated note',
                content='A garden diary describes flowers and the weather.',authorship='external'))
            checks=[]
            for mode in ('keyword','bm25','dense','rrf'):
                result=await kernel.search(MemorySearch(query='Atlas storage database failure',
                    domains=['library'],memory_mode=mode))
                diagnostic=result['retrieval']['library']
                passed=bool(result['evidence'] and result['evidence'][0]['source_id']==target['id'])
                if mode in {'dense','rrf'}:
                    passed=passed and diagnostic['embedding_attempted'] and diagnostic['dense_available'] and not diagnostic['fallback']
                checks.append({'mode':mode,'passed':passed,'dense_available':diagnostic['dense_available'],
                    'embedding_attempted':diagnostic['embedding_attempted'],'fallback':diagnostic['fallback']})
            return {'data_kind':'synthetic_functional','api_calls':0,'checks':checks,
                    'passed':sum(bool(c['passed']) for c in checks),'total':4,
                    'core_fingerprint':core_fingerprint(root)}
        finally:store.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',type=Path,default=Path('model/bge-small-en-v1.5'))
    args=parser.parse_args()
    result=asyncio.run(check(args.model))
    print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(0 if result['passed']==4 else 1)
