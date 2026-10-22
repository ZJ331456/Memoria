# Memoria

<p align="center"><img src="assets/brand/logo-main-lockup.png" alt="Memoria" width="360"></p>

2026-10-23: [Two-arm kernel evaluation and offline cost planning](docs/implemented/双组内核评估与费用估算.md). No new paid calls or full run; current scores are pending. Old published result assets are retired with immutable Git provenance. Cache replay is experimental; production verifier/repair fallbacks remain conservative.

**Source-backed memory for long-term companionship.**

[中文](README.md) · [Getting started](#getting-started) · [Architecture](#architecture) · [Documentation](docs/README.md)

Memoria connects original evidence, review, conditions and revisions so that bounded-context answers can use traceable, applicable memories. It includes independent Chat and Memory Studio interfaces, an authenticated HTTP API, and research tools. The current backend uses DeepSeek with local BGE embeddings. Local Qwen and other OpenAI-compatible configurations remain supported.

![Memoria — Memory that stays in context.](assets/images/memoria-overview.png)

The current scope is a local research prototype and a controlled-pilot foundation. Longitudinal quality, positive profile-view benefits and commercial readiness remain unverified.

[Ordinary observation semantics](docs/implemented/普通观察主体与事件时间.md) now preserve subject, source role, epistemic status and event intervals separately from original-source and recording timestamps. Conversation and document readers use the same metadata; unknown dates remain unknown. This is functional coverage, with extraction and longitudinal quality still unverified.

[Missing-evidence retrieval](docs/implemented/缺项驱动补检与依赖闭合回答.md) connects frozen query requirements, quoted coverage checks, one bounded supplemental round, dependency-closed selection and final answer verification. Chat, bots and source readers share the production loop and inference budget. RRF remains intact; LLM reranking stays off by default. General planning and quality/cost improvements remain unverified.

## Architecture

The [MemoryKernel](docs/implemented/独立记忆核心与多来源接口.md) can be used without a chat runtime or web server. Conversation quotes, derived memories, documents and task results share a bounded evidence interface while retaining their authorship, scope and revision rules. Chat and task applications are clients of this core; documents do not automatically become personal facts.

[Text ingestion and source-aware formation](docs/implemented/文本解析与跨资料记忆形成.md) now support TXT, Markdown, CSV, JSON/JSONL and Word DOCX text. Bounded observation/review stages feed conditional cross-document consolidation and immutable revisions with complete source groups. No PDF/image recognition or OCR is included; quality and longitudinal benefits remain unverified.

![How Memoria works — identity, memory runtime, models and storage](assets/images/memoria-architecture.png)

Claims use typed slots and conditions; ordinary memories use BM25, Dense and RRF retrieval. Evidence budgets and dependency checks constrain publication. Recognized compound recall questions use the bounded sufficiency loop; simple reads retain their existing path. See [implementation and limits](docs/implemented/系统架构与实现说明.md). [Poster PNG](assets/images/memoria-overview.png) · [Vector architecture SVG](assets/images/memoria-architecture.svg).

Account-local watermarks, frozen-copy/fixed-pool replay, complete source groups, optional original-experience Dense/RRF, and conservative narrative-update vetoes are now available. The [implementation and tiny budget diagnostic](docs/implemented/生产重放与普通来源支持.md) separate engineering evidence from unverified quality; the complete LoCoMo results below remain frozen.

## Getting started

Windows PowerShell; the locally verified environment uses Python 3.13.5 and Node.js 22.16.0. Keep an existing virtual environment, private configuration and account data.

```powershell
git clone https://github.com/ZJ331456/Memoria.git
Set-Location Memoria
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Get-ChildItem config/*.example.toml | ForEach-Object {
    $taskConfigFile = $_.FullName.Replace('.example.toml', '.toml')
    if (-not (Test-Path -LiteralPath $taskConfigFile)) {
        Copy-Item -LiteralPath $_.FullName -Destination $taskConfigFile
    }
}
```

Configure models before starting. The example defaults to a local Qwen/BGE HTTP service on port 8080. Alternatively, use a remote main/fast LLM with in-process local BGE. Model weights are excluded from Git; per-account overrides take priority. See the [complete setup guide](docs/implemented/安装配置与启动.md).

The single [requirements.txt](requirements.txt) groups service, local-model, vector, Feishu, scoring, chart and test dependencies; the install command includes every group. Select an appropriate official PyTorch CPU/CUDA wheel before installing the full list. Frontend and MCP Node dependencies remain separate.

```powershell
.\scripts\cmd\build_frontend.cmd
.\scripts\cmd\start_backend.cmd
.\scripts\cmd\start_backend.cmd -Action Status
.\scripts\cmd\stop_backend.cmd
```

Windows entry points live in [scripts/cmd](scripts/cmd/README.md) and support double-click usage. [scripts/sh](scripts/sh/README.md) is reserved for future Linux launchers; none is implemented yet.

Chat: http://127.0.0.1:2237/ · Studio: http://127.0.0.1:2237/studio/ · API docs: http://127.0.0.1:2237/docs

Both interfaces require normal account registration/login; there are no default credentials. MCP tools and the Feishu private-message channel are optional and require their own configuration and account authorization.

## Two-arm LoCoMo evaluation

The proposed v1 compares raw full history (declared oldest-turn truncation when needed) with the production MemoryKernel and the same DeepSeek Flash model. Binary LLM judging uses an adapted permissive rubric; categories 1–4 (1,540 questions) form the primary score, with 446 adversarial questions reported separately.

**No v1 scores exist yet.** The owner requested an estimate before any full paid run. The planning scenario is USD15.37–30.74 including formation, verification, answers and judging; this is not a billing guarantee. [Protocol](eval/protocols/kernel-vs-context-judge-v1.json) · [Limits and estimate](docs/implemented/双组内核评估与费用估算.md) · [Retired records](assets/evaluation/README.md)

## Repository

[Production closed-loop diagnostics](docs/implemented/生产记忆闭环与逐题归因.md) now re-execute the real formation, admission, Claim/View or source Reader on an isolated chronological prefix. Cases preserve source/version lineage, candidates, selection, citations, usage and failures; labels are read only afterward. Existing LoCoMo scores still describe its separate PrefixRetriever protocol.

- `assets/`: project diagrams, retained result summaries and source manifests.
- `docs/implemented/`: current behavior, operations, evidence and limitations.
- `docs/plans/`: research and readiness work awaiting verification.
- `frontend/`, `memoria/`, `scripts/`, `tests/`: interfaces, implementation, tools and engineering checks.
- Ignored `data/` and `model/`: per-account SQLite/WAL, private evidence, credentials, run ledgers and model weights.

Algorithm focus: source-aware formation, cross-session/document consolidation, constrained evolution, then matched-budget quality and cost evaluation. Text ingestion, observations, complete support groups and revision controls are implemented; real support/counterevidence, false merges, false replacement, retraction leakage and task reuse still need evaluation. Stored counts and engineering tests do not establish longitudinal benefit or algorithmic superiority. [Code and seven-project comparison (Chinese)](docs/implemented/算法现状与同类项目对照.md) · [Deliverables and ablations (Chinese)](docs/plans/下一阶段算法路线与对照实验.md)

Continuous personal-history effects remain unverified. Backup/restore, migrations, account lifecycle and monitoring remain on the readiness plan. [Plan](docs/plans/长期陪伴记忆研究与实验计划书.md) · [Sources and licensing](assets/data-sources/README.md) · [AI instructions](AGENTS.md)

Routine memories now use model-led admission: extraction, a separate evidence review, and source/risk checks before automatic low-risk publication. Human review handles sensitive, conflicting or ambiguous cases. Studio supports an explicit per-message history request and audit receipts; public corpus data stays separate. See [implementation and limits](docs/implemented/LLM自动审核与人工辅助.md).

Runtime settings live in [config/](config/README.md): system.toml, models.toml, bot.toml and mcp.toml, each with a public example. Actual files are ignored; there is no local subdirectory. One model file selects cloud/local LLMs and API/process-local BGE embeddings, including UUID-owned account overrides. Encrypted credentials and OAuth remain in private data storage. [Configuration guide](docs/implemented/配置目录与模型后端.md).

Engineering review at core4c8025b72f5c: 1,552 tests, zero failures/errors/skips; real local BGE 4/4 without Dense/RRF fallback. No new paid model calls. The real-model functional suite was not rerun under the budget hold, so complete core acceptance remains pending.
