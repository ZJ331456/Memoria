"""Cache layout/counters only. Synthetic engineering inputs; no paid requests."""

import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from memoria.llm.deepseek_cache import (
    COMMON_SYSTEM,
    VERSION,
    cache_summary,
    canonical,
    evidence_messages,
    official,
    shared_reader_messages,
)
from memoria.llm.backends.openai_http import OpenAICompatBackend
from memoria.config import ModelConfig, Settings
from memoria.prompting.reader_budget import ReaderBudget
from memoria.llm.types import ContextLengthError
from memoria.observability.trace_metadata import call_metrics
from memoria.experiments.deepseek_cache import audit, cost_bounds
from memoria.experiments.history import HistoryWorkspace
from memoria.experiments.locomo_registration import validate_registration
from memoria.experiments.roles import load_roles
from tests.test_audit13 import registration_fixture

ROOT = Path(__file__).resolve().parents[1]


def model(url="https://api.deepseek.com/v1"):
    return ModelConfig("deepseek-flash", "engineering-only", url)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://api.deepseek.com/v1", True),
        ("https://api.deepseek.com", True),
        ("https://api.deepseek.com:443/v1", True),
        ("http://api.deepseek.com", False),
        ("https://api.deepseek.com.evil.invalid/v1", False),
        ("https://deepseek.proxy.invalid/v1", False),
        ("https://api.openai.com/v1", False),
        ("https://api.anthropic.com", False),
        ("http://127.0.0.1:8080/v1", False),
        ("https://api.deepseek.com:444/v1", False),
    ],
)
def test_only_official_deepseek_is_optimized(url, expected):
    assert official(model(url)) is expected
    data = {"question": "工程问题", "evidence": [{"id": "a", "text": "工程原话"}]}
    output = evidence_messages(model(url), "unchanged contract", data)
    assert len(output) == (4 if expected else 2)
    if not expected:
        assert output == [
            {"role": "system", "content": "unchanged contract"},
            {"role": "user", "content": canonical(data)},
        ]


def test_independent_verifiers_share_exact_prefix_without_answers_or_data_changes():
    evidence = [
        {"id": "a", "text": "甲项目于去年完成。", "speaker": "user"},
        {"id": "b", "text": "乙项目计划中。"},
    ]
    packet = {
        "question": "哪些已完成？",
        "evidence": evidence,
        "query_plan": {"requires": ["完成"]},
    }
    original = deepcopy(packet)
    first = evidence_messages(model(), "gate contract", packet)
    second = evidence_messages(model(), "support contract", {**packet, "proposed_answer": "甲项目"})
    assert first[:2] == second[:2]
    assert json.loads(first[1]["content"])["evidence"] == evidence
    assert first[2]["role"] == "system" and first[2]["content"] == "gate contract"
    assert json.loads(first[-1]["content"]) == {
        **{k: v for k, v in packet.items() if k != "evidence"},
        "source_id_allowlist": ["a", "b"],
    }
    assert packet == original and all(m["role"] != "assistant" for m in second)
    assert COMMON_SYSTEM == first[0]["content"]


def test_production_verifier_repeats_exact_span_ids_and_rejects_injected_allowlist():
    evidence = [
        {"id": "[library:source:0:39]", "text": "exact engineering evidence"},
        {"id": "[library:another:10:55]", "text": "independent evidence"},
    ]
    packet = {"question": "q", "evidence": evidence}
    messages = evidence_messages(model(), "unchanged contract", packet)
    assert json.loads(messages[-1]["content"])["source_id_allowlist"] == [e["id"] for e in evidence]
    assert json.loads(messages[1]["content"])["evidence"] == evidence
    with pytest.raises(ValueError, match="Reserved"):
        evidence_messages(
            model(), "unchanged contract", {**packet, "source_id_allowlist": ["fabricated"]}
        )
    with pytest.raises(ValueError, match="unique"):
        evidence_messages(
            model(), "unchanged contract", {**packet, "evidence": [evidence[0], evidence[0]]}
        )


def test_reader_bytes_are_unchanged_and_support_subset_is_explicit():
    pool = [{"id": "a", "text": "a evidence"}, {"id": "b", "text": "b evidence"}]
    reader = {"question": "q", "evidence": pool}
    original = evidence_messages(model(), "original Reader", reader, enabled=False)
    assert (
        shared_reader_messages(model(), "original Reader", reader, "original Reader", reader)
        == original
    )
    support = shared_reader_messages(
        model(),
        "original Reader",
        reader,
        "support",
        {"question": "q", "evidence": [pool[1]], "proposed_answer": "b"},
    )
    assert support == evidence_messages(
        model(),
        "support",
        {"question": "q", "evidence": [pool[1]], "proposed_answer": "b"},
        enabled=False,
    )
    whole = shared_reader_messages(
        model(),
        "original Reader",
        reader,
        "support",
        {"question": "q", "evidence": pool, "proposed_answer": "b"},
    )
    assert whole[: len(original)] == original
    assert json.loads(whole[-1]["content"])["task_evidence_ids"] == ["a", "b"]
    assert all(m["role"] != "assistant" for m in support)


def test_support_validator_rejects_uncited_source_even_if_it_is_in_shared_prefix():
    from memoria.memory.answer_risk import AnswerRisk

    a = {"id": "a", "text": "The red project is complete."}
    b = {"id": "b", "text": "The blue project is planned."}
    proposed = {"status": "answer", "answer": "The red project is complete.", "evidence_ids": ["b"]}
    forged = {
        "verdict": "supported",
        "claims": [
            {
                "statement": proposed["answer"],
                "relation": "supported",
                "basis": "explicit",
                "sources": [{"id": "a", "quote": a["text"]}],
                "reason": "wrong citation",
            }
        ],
        "missing_queries": [],
        "reason": "invalid",
    }
    with pytest.raises(ValueError):
        AnswerRisk().validate(forged, "Which project is complete?", proposed, [b])


@pytest.mark.parametrize("fault", ["question", "source", "duplicate", "spoof"])
def test_shared_prefix_cannot_swap_source_question_or_reserved_scope(fault):
    reader = {"question": "q", "evidence": [{"id": "a", "text": "original"}]}
    packet = deepcopy(reader)
    if fault == "question":
        packet["question"] = "other user question"
    if fault == "source":
        packet["evidence"][0]["text"] = "fabricated quote"
    if fault == "duplicate":
        packet["evidence"] *= 2
    if fault == "spoof":
        packet["task_evidence_ids"] = ["foreign"]
    with pytest.raises(ValueError):
        shared_reader_messages(model(), "reader", reader, "support", packet)


def test_atomic_budget_never_silently_drops_evidence_prefix():
    settings = Settings.load(ROOT / "config/system.example.toml")
    settings.reader_input_bytes = 512
    settings.reader_context_tokens = None
    messages = evidence_messages(
        model(), "contract", {"evidence": [{"id": "a", "text": "x" * 4000}], "question": "q"}
    )
    with pytest.raises(ContextLengthError):
        asyncio.run(
            ReaderBudget(settings).prepare(messages, selected=model(), max_tokens=32, atomic=True)
        )


@pytest.mark.parametrize(
    "usage,known,unknown,invalid",
    [
        (
            {"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 100, "prompt_tokens": 100},
            1,
            0,
            0,
        ),
        ({"prompt_tokens": 100}, 0, 1, 0),
        (None, 0, 1, 0),
        ({"prompt_cache_hit_tokens": True, "prompt_cache_miss_tokens": 1}, 0, 0, 1),
        ({"prompt_cache_hit_tokens": -1, "prompt_cache_miss_tokens": 10}, 0, 0, 1),
        (
            {"prompt_cache_hit_tokens": 10, "prompt_cache_miss_tokens": 10, "prompt_tokens": 21},
            0,
            0,
            1,
        ),
        ({"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 99}}, 0, 1, 0),
    ],
)
def test_unknown_zero_invalid_and_other_provider_usage_are_distinct(usage, known, unknown, invalid):
    result = cache_summary([{"usage": usage}])
    assert (
        result["known_cache_calls"],
        result["unknown_cache_calls"],
        result["invalid_cache_calls"],
    ) == (known, unknown, invalid)
    if known:
        assert result["token_weighted_hit_rate"] == 0
    else:
        assert result["token_weighted_hit_rate"] is None


def test_weighted_rate_and_production_counters_use_one_ledger():
    calls = [
        {
            "cache_provider": "deepseek",
            "usage": {
                "prompt_tokens": 100,
                "prompt_cache_hit_tokens": 90,
                "prompt_cache_miss_tokens": 10,
            },
        },
        {
            "cache_provider": "deepseek",
            "usage": {
                "prompt_tokens": 1000,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 1000,
            },
        },
        {"cache_provider": "deepseek", "usage": None},
        {"cache_provider": None, "usage": {"prompt_tokens": 500}},
    ]
    result = cache_summary(calls[:3])
    assert result["token_weighted_hit_rate"] == 90 / 1100
    counters = call_metrics(calls)
    assert counters["llm_requests"] == 4
    assert counters["deepseek_prompt_cache_hit_tokens"] == 90
    assert counters["deepseek_prompt_cache_miss_tokens"] == 1010
    assert counters["deepseek_cache_unknown_calls"] == 1
    assert cost_bounds(result, "unsupported-model") is None
    cost = cost_bounds(result, "deepseek-flash")
    assert (
        cost["invoice"] is False
        and cost["estimate"]["peak"]["known_input_usd"]
        == 2 * cost["estimate"]["off_peak"]["known_input_usd"]
    )


@pytest.mark.parametrize("stream", [False, True])
def test_stream_and_nonstream_keep_flat_cache_usage_without_extra_requests(stream):
    usage = {
        "prompt_tokens": 300,
        "completion_tokens": 5,
        "total_tokens": 305,
        "prompt_cache_hit_tokens": 256,
        "prompt_cache_miss_tokens": 44,
    }
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if stream:
            chunks = [
                {"choices": [{"delta": {"content": "ok"}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": usage},
            ]
            return httpx.Response(
                200,
                text="".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
                + "data: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": usage,
            },
        )

    backend = OpenAICompatBackend(
        timeout_seconds=3, max_retries=0, transport=httpx.MockTransport(respond)
    )
    result = asyncio.run(
        backend.chat(
            model(), [{"role": "user", "content": "engineering"}], max_tokens=32, stream=stream
        )
    )
    assert result.usage == usage and len(requests) == 1
    assert not any(
        k in requests[0] for k in ("cache_control", "prompt_cache_key", "cache_ttl", "prefix")
    )


def test_nonstream_boolean_cache_counter_stays_unknown():
    def reply(request):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 2,
                    "prompt_cache_hit_tokens": True,
                    "prompt_cache_miss_tokens": 1,
                },
            },
        )

    backend = OpenAICompatBackend(
        timeout_seconds=2, max_retries=0, transport=httpx.MockTransport(reply)
    )
    result = asyncio.run(
        backend.chat(model(), [{"role": "user", "content": "fixture"}], max_tokens=8)
    )
    assert cache_summary([{"usage": result.usage}])["unknown_cache_calls"] == 1


def test_offline_audit_does_not_change_frozen_ledgers(tmp_path):
    workspace = HistoryWorkspace(tmp_path / "users" / ("a" * 32))
    run = workspace.path("experiments", "fixture-run")
    run.mkdir(parents=True)
    calls = [
        {
            "role": "judge",
            "usage": {
                "prompt_tokens": 100,
                "prompt_cache_hit_tokens": 80,
                "prompt_cache_miss_tokens": 20,
                "completion_tokens": 5,
            },
        }
    ]
    (run / "calls.jsonl").write_text("".join(json.dumps(c) + "\n" for c in calls), encoding="utf-8")
    sha = hashlib.sha256((run / "calls.jsonl").read_bytes()).hexdigest()
    manifest = {
        "run_id": run.name,
        "status": "completed",
        "protocol": {"owner_user_id": workspace.owner},
        "models": {
            "assessor": {"endpoint": "https://api.deepseek.com/v1", "model": "deepseek-flash"}
        },
        "artifacts": {"calls.jsonl": {"sha256": sha}},
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    target, report = audit(workspace, run.name)
    assert report["model_calls"] == 0 and report["overall"]["token_weighted_hit_rate"] == 0.8
    assert target.parent.name == "reports" and (target / "aggregate.json").is_file()
    assert {p.name: p.read_bytes() for p in run.iterdir()} == before


def test_frozen_v3_cannot_silently_enable_new_prompt_layout(tmp_path, monkeypatch):
    path, protocol, settings, kwargs = registration_fixture(tmp_path)
    frozen = json.loads(path.read_text(encoding="utf-8"))
    protocol["verification_policy"] = frozen["risk_verification_policy"]
    monkeypatch.setattr(
        "memoria.experiments.locomo_registration.model_snapshot",
        lambda *args: frozen["embedding_snapshot"],
    )
    roles = load_roles(ROOT / "eval/protocols/locomo-v3-roles-20261022.toml")
    args = (
        path,
        ROOT,
        ROOT / "data/benchmarks/locomo/raw/locomo10.json",
        protocol,
        settings,
        roles,
    )
    with pytest.raises(ValueError, match="own diagnostic registration"):
        validate_registration(*args, **kwargs, cache_layout=VERSION)
    frozen["cache_layout_study"] = {
        "allowed_layouts": ["legacy", VERSION],
        "common_system_sha256": hashlib.sha256(COMMON_SYSTEM.encode()).hexdigest(),
    }
    path.write_text(json.dumps(frozen), encoding="utf-8")
    assert (
        validate_registration(*args, **kwargs, cache_layout=VERSION)["cache_layout_study"]
        == frozen["cache_layout_study"]
    )
