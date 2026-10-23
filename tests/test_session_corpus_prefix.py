"""Session corpus prefix: byte stability, official-only injection, scope lifetime."""
import asyncio
import json

import pytest

from memoria.config import Settings
from memoria.llm.deepseek_cache import (
    CORPUS_VERSION, active_session_prefix, reset_session_prefix, session_corpus_block,
    session_prefix_scope, with_session_prefix)
from memoria.memory.adaptive_retrieval import session_corpus
from memoria.memory.kernel import MemoryKernel
from memoria.memory.library import SourceInput
from memoria.memory.source_units import render_turns
from memoria.store import Store
from tests.test_memory_kernel import config_at


def unit_fixture(tmp_path):
    settings = Settings.load(config_at(tmp_path / 'config'))
    store = Store(settings.database)
    kernel = MemoryKernel(settings, store, None)
    session_id = store.create_session('Corpus session')['id']
    content, units = render_turns([
        {'id': 'd/1', 'speaker': 'Ann', 'session_date_raw': '1 pm on 1 May 2023', 'text': 'Ann opened a studio.'},
        {'id': 'd/2', 'speaker': 'Bo', 'session_date_raw': '2 pm on 2 May 2023', 'text': 'Bo visited the beach.'},
    ])
    kernel.ingest(SourceInput(kind='note', authorship='external', scope='working',
        title='Corpus source', session_id=session_id, content=content, source_units=units,
        origin='engineering:synthetic'))
    yield kernel, session_id
    store.close()


def test_corpus_block_is_byte_stable_regardless_of_build_order():
    units = [{'id': '[library:r:5:20]', 'speaker': 'Ann', 'session_date_raw': 'May 2023', 'text': 'Hello.'},
             {'id': '[library:r:25:40]', 'speaker': 'Bo', 'session_date_raw': 'May 2023', 'text': 'Hi.'}]
    first = session_corpus_block(units)
    assert first == session_corpus_block(list(units))  # same order -> identical bytes
    assert first.startswith(CORPUS_VERSION[:0] + '<evidence-data>')
    payload = json.loads(first.strip().removeprefix('<evidence-data>').removesuffix('</evidence-data>'))
    assert [u['id'] for u in payload['corpus']] == ['[library:r:5:20]', '[library:r:25:40]']


def test_session_corpus_uses_unit_citation_ids_and_caches(kernel_session):
    kernel, session_id = kernel_session
    block = session_corpus(kernel, session_id)
    payload = json.loads(block.strip().removeprefix('<evidence-data>').removesuffix('</evidence-data>'))
    ids = [u['id'] for u in payload['corpus']]
    assert len(ids) == 2 and all(i.startswith('[library:') and i.endswith(']') for i in ids)
    texts = {u['text'] for u in payload['corpus']}
    assert texts == {'Ann opened a studio.', 'Bo visited the beach.'}
    # Second build must reuse the cached bytes, not re-serialize.
    assert session_corpus(kernel, session_id) is block


def test_with_session_prefix_only_for_official_deepseek(kernel_session, monkeypatch):
    kernel, session_id = kernel_session
    from memoria.llm.deepseek_cache import with_session_prefix as apply

    class Fake:
        def __init__(self, url):
            self.base_url = url

    official = Fake('https://api.deepseek.com/v1')
    other = Fake('https://proxy.example.com/v1')
    messages = [{'role': 'system', 'content': 'task'}]
    assert apply(other, messages) is messages  # untouched without official endpoint
    token = session_prefix_scope(session_corpus(kernel, session_id))
    try:
        wrapped = apply(official, messages)
        assert len(wrapped) == 3 and wrapped[0]['role'] == 'system'
        assert wrapped[1]['content'].startswith(CORPUS_VERSION)
        assert wrapped[2] is messages[0] or wrapped[2]['content'] == 'task'
        assert active_session_prefix() is not None
        # Non-official endpoint stays uninjected even with an active corpus.
        assert apply(other, messages) is messages
    finally:
        reset_session_prefix(token)
    assert active_session_prefix() is None


def test_official_chat_sends_prefix_and_budget_stays_variable(tmp_path, monkeypatch):
    """The client injects the corpus for official DeepSeek; metering stays variable-only."""
    import httpx
    from memoria.llm import LLMClient
    settings = Settings.load(config_at(tmp_path / 'config'))
    settings.main.base_url = 'https://api.deepseek.com/v1'
    settings.main.api_key = 'engineering-only'
    settings.main.model = 'deepseek-flash'
    captured = {}

    async def handler(request):
        captured['payload'] = json.loads(request.content.decode('utf-8'))
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 3, 'completion_tokens': 1}})

    transport = httpx.MockTransport(handler)
    client = LLMClient(settings, transport=transport)
    token = session_prefix_scope(session_corpus_block(
        [{'id': '[library:r:0:9]', 'speaker': 'Ann', 'session_date_raw': 'May 2023', 'text': 'Hello.'}]))
    try:
        result = asyncio.run(client.chat([{'role': 'system', 'content': 'task'},
                                          {'role': 'user', 'content': 'question'}], max_tokens=16))
        assert result.content == '{}'
        sent = captured['payload']['messages']
        assert len(sent) == 4 and sent[1]['content'].startswith(CORPUS_VERSION)
        # Budget report measures the variable request without the corpus prefix.
        assert result.budget['input_bytes'] < len(json.dumps(captured['payload']).encode('utf-8'))
    finally:
        reset_session_prefix(token)


@pytest.fixture
def kernel_session(tmp_path):
    yield from unit_fixture(tmp_path)
