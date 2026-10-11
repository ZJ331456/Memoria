"""Real local GPU/HTTP/SDK smoke test using synthetic inputs and no personal DB."""
from __future__ import annotations

import argparse
import json
import math
import socket
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", choices=["cuda", "cpu", "auto"], default="cuda")
    parser.add_argument("--output", type=Path, default=ROOT / "data/reports/local_openai_compat_smoke.json")
    args = parser.parse_args()
    from openai import DefaultHttpxClient, OpenAI

    args.output.parent.mkdir(parents=True, exist_ok=True)
    log_path = args.output.with_suffix(".server.log")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    command = [sys.executable, str(ROOT / "scripts/serve_local_llm.py"), "--device", args.device,
               "--port", str(port), "--served-model-name", "Qwen3.5-2B", "--warmup"]
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            url = f"http://127.0.0.1:{port}/v1"
            with httpx.Client(trust_env=False, timeout=2) as http:
                while time.perf_counter() - started < 180:
                    if process.poll() is not None:
                        raise RuntimeError(f"server exited; inspect {log_path}")
                    try:
                        if http.get(url.removesuffix("/v1") + "/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.25)
                else:
                    raise TimeoutError("local server startup timed out")
            ready_ms = round((time.perf_counter() - started) * 1000, 3)
            with OpenAI(base_url=url, api_key="local", max_retries=0, timeout=120,
                        http_client=DefaultHttpxClient(trust_env=False)) as client:
                ids = [item.id for item in client.models.list().data]
                assert "Qwen3.5-2B" in ids and "bge-small-zh-v1.5" in ids
                chat = client.chat.completions.create(model="Qwen3.5-2B", messages=[
                    {"role": "user", "content": "只回答：本地接口已连接"}], max_tokens=32, temperature=0)
                assert chat.choices[0].message.content and chat.usage.total_tokens > 0
                fragments, finish, usage, first_text_ms = [], None, None, None
                streamed_at = time.perf_counter()
                stream = client.chat.completions.create(model="Qwen3.5-2B", messages=[
                    {"role": "user", "content": "用一句话解释什么是长期记忆。"}], max_completion_tokens=48,
                    stream=True, stream_options={"include_usage": True}, temperature=0)
                with stream:
                    for event in stream:
                        if event.usage:
                            usage = event.usage.model_dump()
                        if event.choices:
                            choice = event.choices[0]
                            if choice.delta.content:
                                fragments.append(choice.delta.content)
                                if first_text_ms is None:
                                    first_text_ms = round((time.perf_counter() - streamed_at) * 1000, 3)
                            finish = choice.finish_reason or finish
                assert fragments and finish in {"stop", "length"} and usage
                embedding = client.embeddings.create(model="bge-small-zh-v1.5", input=["用户喜欢喝茶。", "用户养了一只猫。"])
                assert len(embedding.data) == 2 and all(len(item.embedding) == 512 for item in embedding.data)
                norms = [math.sqrt(sum(value * value for value in item.embedding)) for item in embedding.data]
                assert all(abs(norm - 1) < 1e-5 for norm in norms)
                tools = [{"type": "function", "function": {"name": "calculate", "description": "计算数学表达式",
                          "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}}]
                tool_reply = client.chat.completions.create(model="Qwen3.5-2B", messages=[
                    {"role": "user", "content": "必须调用 calculate 工具计算表达式 1+2，不要口算。"}],
                    tools=tools, tool_choice="auto", max_tokens=128, temperature=0)
                calls = tool_reply.choices[0].message.tool_calls or []
                followup = None
                if calls:
                    assert calls[0].function.name == "calculate"
                    assert json.loads(calls[0].function.arguments)["expression"] == "1+2"
                    reply = client.chat.completions.create(model="Qwen3.5-2B", messages=[
                        {"role": "user", "content": "必须调用 calculate 工具计算表达式 1+2，不要口算。"},
                        tool_reply.choices[0].message.model_dump(exclude_none=True, exclude={"refusal", "annotations", "audio", "function_call"}),
                        {"role": "tool", "tool_call_id": calls[0].id, "content": "3"}], max_tokens=32)
                    followup = reply.choices[0].message.content
                report = {"ok": True, "kind": "synthetic-local-http-sdk-smoke", "external_api_calls": 0,
                          "personal_database_used": False, "python_executable": sys.executable, "device": args.device,
                          "openai_sdk": version("openai"), "torch": version("torch"), "transformers": version("transformers"),
                          "server_ready_including_warmup_ms": ready_ms, "models": ids,
                          "chat": {"content": chat.choices[0].message.content, "finish_reason": chat.choices[0].finish_reason,
                                   "usage": chat.usage.model_dump()},
                          "stream": {"content": "".join(fragments), "text_chunks": len(fragments), "first_text_ms": first_text_ms,
                                     "finish_reason": finish, "usage": usage},
                          "embedding": {"dimension": 512, "count": 2, "norms": norms, "usage": embedding.usage.model_dump()},
                          "tools": {"finish_reason": tool_reply.choices[0].finish_reason,
                                    "calls": [call.model_dump() for call in calls], "followup": followup},
                          "limitation": "Synthetic interface smoke only, not model quality, load performance or long-term memory evaluation."}
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(json.dumps(report, ensure_ascii=False, indent=2))
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
