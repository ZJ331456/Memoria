"""Isolated real-model Chinese diagnostics and production long-memory smoke.

Starts a temporary hidden loopback service, never opens the personal database,
never calls external APIs, and always terminates its own server.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memoria.config import Settings
from memoria.experiments.records import digest_file
from memoria.memory.embedding import EmbeddingClient
from memoria.memory.engine import MemoryEngine
from memoria.observability.timing import StageTimings
from memoria.prompting import PromptAssembler
from memoria.store import Store
from tests.regression.run_companion import DIRECTORY, run


async def long_memory(config: Path):
    settings = Settings.load(config)
    timings = StageTimings()
    with tempfile.TemporaryDirectory(prefix="memoria-long-chunks-") as temporary:
        store = Store(Path(temporary) / "long.db", "sqlite-vec")
        try:
            engine = MemoryEngine(store, EmbeddingClient(settings.embedding, 120, 0))
            text = "我记录了今天的天气、步行时间和阅读页数，其他日常安排照旧。\n" * 120
            text += "我家的猫叫栗子。栗子患有鸡肉过敏，喂食必须排除鸡肉。"
            with timings.measure("memory.long_write"):
                saved = (await engine.remember(text, "fact", 3, "manual")).memory
            chunks = store.memory_chunks(saved["id"])
            assert len(chunks) > 1 and sum(c["end"] - c["start"] for c in chunks) == len(text)
            assert chunks[-1]["end"] == len(text) and max(c["tokens"] for c in chunks) <= 512
            assert store.vector_index_status["enabled"] and store.chunk_index.enabled
            with timings.measure("memory.retrieve"):
                hits = await engine.retrieve("家里的猫需要避开哪一种肉类？", expand_graph=False, timings=timings)
            hit = next(h for h in hits if h["id"] == saved["id"])
            assert hit["retrieval"]["vector_rank"] is not None
            assert "鸡肉过敏" in hit["retrieval_excerpt"]
            with timings.measure("prompt.assemble"):
                frame = PromptAssembler(max_frame_chars=900).assemble("system", memories=hits).context_frame["content"]
            assert "鸡肉过敏" in frame
            with timings.measure("memory.long_correct"):
                corrected = await engine.correct(saved["id"], text.replace("鸡肉", "牛肉"), "fact", 3, "用户更正")
            new_hits = await engine.retrieve("家里的猫需要避开哪一种肉类？", expand_graph=False)
            assert saved["id"] not in {h["id"] for h in new_hits}
            assert any(h["id"] == corrected["id"] and "牛肉过敏" in h.get("retrieval_excerpt", "") for h in new_hits)
            return {"ok": True, "source_chars": len(text), "chunks": len(chunks),
                    "max_chunk_tokens": max(c["tokens"] for c in chunks), "coverage": 1.0,
                    "matched_span": hit["retrieval"]["chunk"],
                    "vector_similarity": hit["retrieval"]["vector_similarity"],
                    "vector_backend": store.vector_index_status, "tail_survived_prompt": True,
                    "correction_excluded_old_parent": True, "stage_timings": timings.public_dict(),
                    "scope": "single synthetic memory; coverage/lifecycle/interface smoke, not retrieval quality or scale performance"}
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=["cuda", "cpu", "auto"], default="cuda")
    parser.add_argument("--output", type=Path, default=ROOT / "data/reports/companion_local.json")
    parser.add_argument("--mode", choices=["both", "reviewed", "extracted"], default="both")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    command = [sys.executable, str(ROOT / "scripts/serve_local_llm.py"), "--device", args.device,
               "--port", str(port), "--warmup"]
    log_path = args.output.with_suffix(".server.log")
    started = time.perf_counter()
    report = {"external_api_calls": 0, "personal_database_used": False, "runs": []}
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            url = f"http://127.0.0.1:{port}/v1"
            with httpx.Client(trust_env=False, timeout=2) as http:
                while time.perf_counter() - started < 180:
                    if process.poll() is not None:
                        raise RuntimeError(f"local service exited; inspect {log_path}")
                    try:
                        if http.get(url.removesuffix("/v1") + "/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.25)
                else:
                    raise TimeoutError("local service startup timed out")
            ready_ms = round((time.perf_counter() - started) * 1000, 3)
            deployment = {"owned_temporary_server": True, "device": args.device,
                          "entrypoint": "scripts/serve_local_llm.py",
                          "entrypoint_sha256": digest_file(ROOT / "scripts/serve_local_llm.py"),
                          "llm": "model/Qwen3.5-2B", "embedding": "model/bge-small-zh-v1.5",
                          "max_context_tokens": 8192, "request_timeout_seconds": 300,
                          "warmup": True, "thinking": False,
                          "server_ready_including_warmup_ms": ready_ms}
            report["deployment"] = deployment
            with tempfile.TemporaryDirectory(prefix="memoria-local-protocol-") as temporary:
                config = Path(temporary) / "config.toml"
                config.write_text(f'[storage]\ndatabase="{(Path(temporary) / "unused.db").as_posix()}"\n'
                    '[memory.markdown]\nenabled=false\n'
                    f'[llm.main]\nmodel="model/Qwen3.5-2B"\nbase_url="{url}"\n'
                    f'[llm.fast]\nmodel="model/Qwen3.5-2B"\nbase_url="{url}"\n'
                    f'[memory.embedding]\nmodel="bge-small-zh-v1.5"\nbase_url="{url}"\n', encoding="utf-8")
                report["long_memory"] = asyncio.run(long_memory(config))
                for mode in (["reviewed", "extracted"] if args.mode == "both" else [args.mode]):
                    params = argparse.Namespace(config=config, inputs=DIRECTORY / "companion_inputs.json",
                        gold=DIRECTORY / "companion_gold.json", roles=DIRECTORY / "companion_roles.toml",
                        out=ROOT / "data/experiments/companion-local", mode=mode, reader=True, embedding=True,
                        allow_remote=False, vector_backend="sqlite-vec", seed=17, deployment=deployment)
                    path, metrics = asyncio.run(run(params))
                    report["runs"].append({"mode": mode, "path": str(path), "metrics": metrics})
                    print(json.dumps({"mode": mode, "run": str(path), "metrics": metrics}, ensure_ascii=False), flush=True)
            report["ok"] = report["long_memory"]["ok"] and all(not r["metrics"]["errors"] for r in report["runs"])
        except BaseException as exc:
            report.update(ok=False, error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "report": str(args.output)}, ensure_ascii=False))
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
