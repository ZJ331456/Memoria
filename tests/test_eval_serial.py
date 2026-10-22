"""Sequential paid evaluation must stop spending without hiding planned questions."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from memoria.config import Settings, ModelConfig
from memoria.experiments.eval_accounting import EvaluationClient, EvaluationBudgetExceeded
from memoria.experiments.eval_benchmarks import BenchmarkItem
from memoria.experiments.eval_runner import run_arms


def test_two_billing_failures_open_circuit_and_do_not_send_a_third_request():
    root = Path(__file__).resolve().parents[1]
    settings = Settings.load(root / "config/system.example.toml")
    settings.main = ModelConfig("deepseek-flash", "engineering-only", "https://api.deepseek.com/v1")
    settings.max_retries = 0
    sent, rows = [], []
    def handler(request):
        sent.append(request)
        return httpx.Response(402, json={"error": {"message": "Insufficient Balance"}})
    client = EvaluationClient(settings, SimpleNamespace(append=lambda name, row: rows.append((name, dict(row)))),
                              max_calls=10, max_cost_usd=1, transport=httpx.MockTransport(handler))
    async def run():
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await client.chat([{"role": "user", "content": "test"}], max_tokens=32)
        with pytest.raises(EvaluationBudgetExceeded):
            await client.chat([{"role": "user", "content": "never sent"}], max_tokens=32)
    asyncio.run(run())
    assert len(sent) == 2 and client.halt_reason == "repeated_insufficient_balance"
    assert len([r for name, r in rows if name == "calls.jsonl"]) == 2
    assert client.accounting()["halt_reason"] == client.halt_reason


def test_serial_memoria_first_and_stopped_baseline_keeps_every_planned_id():
    order, state, checkpoints = [], {}, []
    class System:
        def __init__(self, name):
            self.name = name
        async def answer(self, query):
            order.append((self.name, query))
            if query == "q2":
                state["halt"] = "repeated_insufficient_balance"
            return {"answer": json.dumps({"status": "answer", "answer": "A", "evidence_ids": []})}
    items = [BenchmarkItem(str(i), "q"+str(i), "A", group="1") for i in range(3)]
    rows = asyncio.run(run_arms({"memoria": System("memoria"), "raw_context": System("raw_context")},
        items, concurrency=1, stop_reason=lambda: state.get("halt"), checkpoint=checkpoints.append))
    assert order == [("memoria", "q0"), ("memoria", "q1"), ("memoria", "q2")]
    assert len(checkpoints) == 6
    assert all(r.error == "not_run:repeated_insufficient_balance" for r in rows["raw_context"])
    assert [r.id for r in rows["raw_context"]] == ["0", "1", "2"]


def test_parallel_histories_are_isolated_bounded_and_whole_memoria_finishes_first():
    from memoria.experiments.eval_runner import run_ordered_histories
    state, order, seen = {'active': 0, 'peak': 0}, [], []
    class System:
        def __init__(self, arm, signature):
            self.arm, self.signature, self.sources = arm, signature, []
        def reset(self, sources):
            self.sources = []
        async def ingest(self, source):
            self.sources.append(source['id'])
        async def answer(self, query):
            state['active'] += 1
            state['peak'] = max(state['peak'], state['active'])
            await asyncio.sleep(.001)
            order.append(self.arm)
            seen.append((self.arm, self.signature, tuple(self.sources)))
            state['active'] -= 1
            return {'answer': '{"status":"answer","answer":"A","evidence_ids":[]}'}
    items = [BenchmarkItem(str(i), 'q', 'A', sources=[{'id': str(i//2), 'text': str(i//2)}]) for i in range(8)]
    rows = asyncio.run(run_ordered_histories({name: (lambda signature, name=name: System(name, signature))
        for name in ['memoria', 'raw_context']}, items, concurrency=2))
    assert state['peak'] == 2
    assert order == ['memoria']*8+['raw_context']*8
    assert all(len(source_ids) == 1 for _, _, source_ids in seen)
    assert len({signature for _, signature, _ in seen}) == 4
    assert [r.id for r in rows['memoria']] == [str(i) for i in range(8)]
