import asyncio
import json
import subprocess
from contextlib import closing

import httpx
import pytest

from memoria.config import Settings
from memoria.experiments.records import RunRecorder, digest_file, safe_endpoint
from memoria.experiments.roles import ExperimentLLM, load_roles
from memoria.observability.timing import StageTimings
from memoria.observability.tracing import TurnTracer
from memoria.store import Store


def test_nested_timers_count_failures_and_do_not_share_state(monkeypatch):
    clock = iter([0, .001, .004, .008])
    monkeypatch.setattr("memoria.observability.timing.time.perf_counter", lambda: next(clock))
    timings = StageTimings()
    with pytest.raises(RuntimeError), timings.measure("outer"), timings.measure("inner"):
        raise RuntimeError("cancelled stage")
    assert timings.public_dict()["stages"] == {
        "inner": {"calls": 1, "duration_ms": 3, "failed": 1},
        "outer": {"calls": 1, "duration_ms": 8, "failed": 1},
    }
    assert StageTimings().stages == {}


def test_trace_ttft_uses_first_visible_text_and_keeps_empty_null(tmp_path, monkeypatch):
    with closing(Store(tmp_path / "trace.db", "json")) as store:
        session = store.create_session("timing")
        clock = iter([1, 2, 3, 4])
        monkeypatch.setattr("memoria.observability.tracing.time.perf_counter", lambda: next(clock))
        tracer = TurnTracer(store, session["id"])
        tracer.mark_llm_request()
        tracer.mark_visible_text("")
        tracer.mark_visible_text("首字")
        tracer.mark_visible_text("后续")
        trace = tracer.finish("completed", 1, [], [])
        assert trace["metadata"]["ttft"]["turn_ms"] == 2000
        assert trace["metadata"]["ttft"]["llm_ms"] == 1000


def test_manifest_hashes_actual_files_and_retains_failed_denominator(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=test", "-c", "user.email=test@example.test",
                    "commit", "--allow-empty", "-qm", "fixture"], check=True)
    data = root / "inputs.json"
    data.write_text('{"x":1}', encoding="utf-8")
    model = root / "model"
    model.mkdir()
    (model / "model.safetensors").write_bytes(b"test weights")
    (model / "tokenizer.json").write_text("{}", encoding="utf-8")
    recorder = RunRecorder(tmp_path / "runs", root, datasets=[data], models={}, protocol={}, seed=1,
                           model_paths={"reader": model})
    recorder.append("calls.jsonl", {"error": "Bearer very_secret", "api_key": "hidden"})
    recorder.finish({"requested_cases": 2, "errors": 1})
    manifest = json.loads((recorder.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed" and manifest["code"]["dirty"]
    assert manifest["datasets"][0]["sha256"] == digest_file(data)
    weights = next(a for a in manifest["model_snapshots"]["reader"]["artifacts"] if a["file"].endswith("safetensors"))
    assert weights["sha256"] == digest_file(model / "model.safetensors")
    assert manifest["artifacts"]["calls.jsonl"]["sha256"] == digest_file(recorder.path / "calls.jsonl")
    assert "hidden" not in (recorder.path / "calls.jsonl").read_text()
    assert safe_endpoint("https://user:password@host.test/v1?key=secret#secret") == "https://host.test/v1"








@pytest.mark.parametrize("stream", [False, True])
def test_roles_send_explicit_generation_settings_and_record_actual_usage(tmp_path, stream):
    config = tmp_path / "config.toml"
    config.write_text(f'[storage]\ndatabase="{(tmp_path / "isolated.db").as_posix()}"\n'
                      '[llm.main]\nmodel="reader-model"\nbase_url="http://127.0.0.1:8080/v1"\n'
                      '[llm.fast]\nmodel="fast-model"\nbase_url="http://127.0.0.1:8080/v1"\n', encoding="utf-8")
    roles = load_roles(None)
    recorded = []

    class Recorder:
        def append(self, file, row):
            recorded.append((file, row))

    def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == "reader-model"
        assert payload["temperature"] == 0 and payload["top_p"] == 1 and payload["max_tokens"] == 512
        usage = {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}
        if stream:
            chunks = [{"choices": [{"index": 0, "delta": {"content": "北京"}, "finish_reason": None}]},
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": usage}]
            return httpx.Response(200, text="".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n",
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "北京"}, "finish_reason": "stop"}], "usage": usage})

    client = ExperimentLLM(Settings.load(config), roles, Recorder(), StageTimings())
    client.transport = httpx.MockTransport(handler)
    result = asyncio.run(client.call("reader", [{"role": "user", "content": "住哪"}], stream=stream))
    assert result.content == "北京" and client.calls[0]["usage"]["total_tokens"] == 3
    assert client.calls[0]["response"] == "北京"
    assert (client.calls[0]["ttft_ms"] is not None) == stream
    assert client.timings.stages["llm.reader"]["calls"] == 1
    assert {file for file, row in recorded} == {"prompts.jsonl", "calls.jsonl"}


def test_truncated_completion_retains_usage_error_and_partial_output_in_private_ledger(tmp_path):
    from memoria.llm.types import IncompleteResponseError
    config=tmp_path/'config.toml'
    config.write_text('[llm.main]\nmodel="fixture"\nbase_url="http://127.0.0.1:8080/v1"\n',encoding='utf-8')
    records=[]
    class Recorder:
        def append(self,file,row):records.append((file,row))
    def handler(request):
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"partial":'},'finish_reason':'length'}],
            'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}})
    client=ExperimentLLM(Settings.load(config),load_roles(None),Recorder(),StageTimings())
    client.transport=httpx.MockTransport(handler)
    with pytest.raises(IncompleteResponseError) as error:
        asyncio.run(client.call('judge',[{'role':'user','content':'engineering input'}]))
    assert error.value.finish_reason=='length'
    assert len(client.calls)==1 and client.calls[0]['usage']['total_tokens']==30
    assert client.calls[0]['finish_reason']=='length' and client.calls[0]['error'].startswith('IncompleteResponseError')
    assert client.calls[0]['response']=='{"partial":'
