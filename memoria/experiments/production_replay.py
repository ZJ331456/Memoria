"""Private frozen production-store diagnostics; no LLM calls, no benchmark gold."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from ..claims import ClaimMemory
from ..memory import MemoryEngine, EmbeddingClient
from ..memory.access import MemoryAccessOrchestrator, QueryRequest
from ..observability.memory_trace import replay, watermark
from ..store import Store
from .history import write_json
from .records import RunRecorder


async def capture_production(workspace, settings, request, *, local_embeddings=False, index_local_chunks=0, embedding_backend="configured"):
    request = QueryRequest.model_validate(request)
    if request.use_query_model or request.budget_unit == "reader_tokens":
        raise ValueError("Capture uses no LLM; use chars/utf8_bytes and disable query model")
    if not local_embeddings and request.mode == "dense":
        raise ValueError("Dense capture requires explicit --local-embeddings")
    if type(index_local_chunks) is not int or not 0<=index_local_chunks<=32 or (index_local_chunks and not local_embeddings):
        raise ValueError("Index only 0..32 chunks with explicit offline local embeddings")
    if embedding_backend not in {"configured","process"} or (embedding_backend=="process" and not local_embeddings):
        raise ValueError("Process override requires explicit local embeddings")
    source = workspace.path("memory", "memoria.db")
    if not source.is_file():
        raise ValueError("Account memory database missing")
    embedder = None
    if local_embeddings:
        import copy
        from ..models_config import apply_overrides
        settings = copy.deepcopy(settings)
        settings.model_override_file = workspace.path("config", "models.override.toml")
        apply_overrides(settings)
        if embedding_backend=="process":
            from ..config import ModelConfig
            name=settings.embedding.model.replace('\\','/').split('/')[-1]
            if name not in {"bge-small-zh-v1.5","bge-small-en-v1.5"} or not (settings.root/"model"/name).is_dir():
                raise ValueError("Process override requires an existing supported local BGE directory")
            settings.embedding=ModelConfig(model=str(settings.root/"model"/name),api_key="",base_url="local://auto")
        embedder = EmbeddingClient(settings.embedding,timeout_seconds=30,max_retries=0)
        from ..memory.experience import local_encoder
        if not local_encoder(embedder):
            raise ValueError("Only configured offline local embeddings are permitted")
    recorder = RunRecorder(workspace.path("experiments"), settings.root, datasets=[],
        models={"embedding_namespace":embedder.namespace if embedder else None, "reader":None},
        protocol={"owner_user_id":workspace.owner, "mode":"production_snapshot_replay",
            "request":request.model_dump(mode="json"), "retrieval_variant":"local_embedding" if embedder else "lexical_only",
            "embedding_backend":embedding_backend,"index_local_chunks":index_local_chunks,
            "reader_called":False, "production_writes":0, "gold_read":False,
            "scope":"same production access code on a frozen account copy; not extraction/QA quality"}, seed=None)
    metrics = {"errors":1, "llm_calls":0, "production_writes":0, "quality":"unscored"}
    fatal = None
    try:
        frozen = recorder.path / "account-snapshot.db"
        readonly = sqlite3.connect(source.resolve().as_uri()+"?mode=ro", uri=True)
        copied = sqlite3.connect(frozen)
        try:
            readonly.backup(copied)
        finally:
            copied.close(); readonly.close()
        # Opening Store may migrate/rebuild indexes: preserve the captured input bytes.
        with tempfile.TemporaryDirectory(prefix="memoria-production-replay-") as temporary:
            working = Path(temporary)/"working.db"
            import shutil
            shutil.copyfile(frozen,working)
            store = Store(working, vector_backend="json")
            try:
                claims = ClaimMemory(store, workspace.owner)
                engine = MemoryEngine(store,embedder)
                if index_local_chunks:
                    from ..memory.experience import reindex_dense
                    metrics["experience_index"] = await reindex_dense(store,embedder,index_local_chunks)
                packet = await MemoryAccessOrchestrator(claims,engine).access(request)
                write_json(recorder.path/"production-capture.json",packet)
                write_json(recorder.path/"selection-replay.json",replay(packet,workspace.owner))
                recorder.append("events.jsonl", {"snapshot_watermark":watermark(store),
                    "historical_event_count":store.db.execute("SELECT COUNT(*) FROM memory_trace_events").fetchone()[0]})
                metrics.update(errors=0,candidate_count=len(packet["candidates"]),
                    presented_count=len(packet["selection"]["payload"]["evidence"]),
                    changed_stages=packet["production_trace"]["changed_stages"],
                    experience_diagnostics=packet["retrieval"].get("experiences"),
                    integrity=store.db.execute("PRAGMA integrity_check").fetchone()[0])
            finally:
                store.close()
    except BaseException as exc:
        fatal=type(exc).__name__
        raise
    finally:
        recorder.finish(metrics,error=fatal)
    return recorder.path


def replay_file(workspace, settings, path, *, budgets=None):
    source = Path(path).resolve(strict=True)
    if not source.is_relative_to(workspace.directory) or source.is_symlink():
        raise ValueError("Capture must be in this account's private directory")
    packet = json.loads(source.read_text(encoding="utf-8"))
    result = replay(packet,workspace.owner,budgets=budgets)
    recorder = RunRecorder(workspace.path("experiments"),settings.root,datasets=[source],models={},
        protocol={"mode":"production_fixed_pool_selection", "owner_user_id":workspace.owner,
                  "model_calls":0,"gold_read":False,"budgets":budgets}, seed=None)
    write_json(recorder.path/"selection-replay.json",result)
    recorder.finish({"errors":0,"model_calls":0,"quality":"unscored","production_writes":0})
    return recorder.path
