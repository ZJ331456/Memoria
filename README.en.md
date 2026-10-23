# Memoria

Latest: [evidence-flow fixes and the original 36-case single-framework retest](docs/implemented/证据流召回判断与回答修复.md): **15/28 ordinary (53.57% lower bound, one unscored budget failure)** and **6/8 adversarial**. Ordinary results increased from 12/28, while adversarial results fell from 8/8; overall quality improvement is not established. Input now includes separate dataset-provided machine captions. Final acceptance: **1764 tests, 49/49 model-backed synthetic checks, and both actual local BGE suites passed**. [One formal group](assets/evaluation/evidence-flow36.json) · [all attempts and costs](assets/evaluation/evidence-flow-acceptance.json). Raw QA and ledgers remain private.

<p align="center"><img src="assets/brand/logo-main-lockup.png" alt="Memoria" width="360"></p>

2026-10-23: The first current [two-arm full evaluation](docs/implemented/双组内核评估与费用估算.md) has executed all planned questions: the entire Memoria arm first, then the full/truncated-context baseline. Independent histories are isolated and bounded in concurrency; repeated insufficient balance stops new requests. Only current aggregate results are published; raw checkpoints, prompts, answers and SQLite remain private.

**Source-backed memory for long-term companionship.**

[中文](README.md) · [Getting started](#getting-started) · [Architecture](#architecture) · [Documentation](docs/README.md)

Memoria connects original evidence, review, conditions and revisions so that bounded-context answers can use traceable, applicable memories. It includes independent Chat and Memory Studio interfaces, an authenticated HTTP API, and research tools. The current backend uses DeepSeek with local BGE embeddings. Local Qwen and other OpenAI-compatible configurations remain supported.

![Memoria — source-to-memory lifecycle.](assets/images/memoria-overview.png)

The current scope is a local research prototype and a controlled-pilot foundation. Longitudinal quality, positive profile-view benefits and commercial readiness remain unverified.

[Ordinary observation semantics](docs/implemented/普通观察主体与事件时间.md) now preserve subject, source role, epistemic status and event intervals separately from original-source and recording timestamps. Conversation and document readers use the same metadata; unknown dates remain unknown. This is functional coverage, with extraction and longitudinal quality still unverified.

[Missing-evidence retrieval](docs/implemented/缺项驱动补检与依赖闭合回答.md) connects frozen query requirements, quoted coverage checks, one bounded supplemental round, dependency-closed selection and final answer verification. Chat, bots and source readers share the production loop and inference budget. RRF remains intact; LLM reranking stays off by default. General planning and quality/cost improvements remain unverified.

## Architecture

The [MemoryKernel](docs/implemented/独立记忆核心与多来源接口.md) can be used without a chat runtime or web server. Conversation quotes, derived memories, documents and task results share a bounded evidence interface while retaining their authorship, scope and revision rules. Chat and task applications are clients of this core; documents do not automatically become personal facts.

[Text ingestion and source-aware formation](docs/implemented/文本解析与跨资料记忆形成.md) now support TXT, Markdown, CSV, JSON/JSONL and Word DOCX text. Bounded observation/review stages feed conditional cross-document consolidation and immutable revisions with complete source groups. No PDF/image recognition or OCR is included; quality and longitudinal benefits remain unverified.

![How Memoria works — identity, memory runtime, models and storage](assets/images/memoria-architecture.png)

Claims use typed slots and conditions; ordinary memories use BM25, Dense and RRF retrieval. Evidence budgets and dependency checks constrain publication. Recognized compound recall questions use the bounded sufficiency loop; simple reads retain their existing path. See [implementation and limits](docs/implemented/系统架构与实现说明.md). [Poster PNG](assets/images/memoria-overview.png) · [Vector architecture SVG](assets/images/memoria-architecture.svg).

Account-local watermarks, frozen-copy/fixed-pool replay, complete source groups, optional original-experience Dense/RRF, and conservative narrative-update vetoes are now available. The [implementation and tiny budget diagnostic](docs/implemented/生产重放与普通来源支持.md) separate engineering evidence from unverified quality; historical scores remain retired from current presentation.

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

The completed [registered full run](eval/protocols/kernel-vs-context-judge-v3-ordered-histories.json) finishes Memoria first, then the full/truncated-context baseline. Same DeepSeek Flash backbone, non-thinking, 1,024-token Reader output. Up to eight independent histories run concurrently inside one arm; questions remain serial inside each history.

Main categories: single-hop841, multi-hop282, open-domain96 and temporal321; Overall is correct/1,540. The446 adversarial cases are supplementary. Independent binary LLM judging uses a frozen adapted permissive rubric, with failures and unknowns retained in the planned denominator. It is not the old Token F1 metric.

See the [live/final classification table](README.md#locomo-全量评测) and [public aggregate](assets/evaluation/current.json). Pending methods are not plotted as measured zero scores. Raw prompts, per-question answers, failures, ledgers and isolated databases remain local. Repeated insufficient balance stops new calls; no automatic top-up or hidden retry.

This is a same-question development retest of the native external-source Kernel path. It does not automatically activate personal Claim/View or Dense/RRF, prove longitudinal understanding, or isolate a single mechanism. The serial preflight stays separate from full-run quality and retains its cost. [Detailed limits](docs/implemented/双组内核评估与费用估算.md).


## Repository

[Production closed-loop diagnostics](docs/implemented/生产记忆闭环与逐题归因.md) now re-execute the real formation, admission, Claim/View or source Reader on an isolated chronological prefix. Cases preserve source/version lineage, candidates, selection, citations, usage and failures; labels are read only afterward. Legacy PrefixRetriever results are retired; the current full run uses the native Kernel path.

- `assets/`: project diagrams, retained result summaries and source manifests.
- `docs/implemented/`: current behavior, operations, evidence and limitations.
- `docs/plans/`: research and readiness work awaiting verification.
- `frontend/`, `memoria/`, `scripts/`, `tests/`: interfaces, implementation, tools and engineering checks.
- Ignored `data/` and `model/`: per-account SQLite/WAL, private evidence, credentials, run ledgers and model weights.

Algorithm focus: source-aware formation, cross-session/document consolidation, constrained evolution, then matched-budget quality and cost evaluation. Text ingestion, observations, complete support groups and revision controls are implemented; real support/counterevidence, false merges, false replacement, retraction leakage and task reuse still need evaluation. Stored counts and engineering tests do not establish longitudinal benefit or algorithmic superiority. [Code and seven-project comparison (Chinese)](docs/implemented/算法现状与同类项目对照.md) · [Deliverables and ablations (Chinese)](docs/plans/下一阶段算法路线与对照实验.md)

Continuous personal-history effects remain unverified. Backup/restore, migrations, account lifecycle and monitoring remain on the readiness plan. [Plan](docs/plans/长期陪伴记忆研究与实验计划书.md) · [Sources and licensing](assets/data-sources/README.md) · [AI instructions](AGENTS.md)

Routine memories now use model-led admission: extraction, a separate evidence review, and source/risk checks before automatic low-risk publication. Human review handles sensitive, conflicting or ambiguous cases. Studio supports an explicit per-message history request and audit receipts; public corpus data stays separate. See [implementation and limits](docs/implemented/LLM自动审核与人工辅助.md).

Runtime settings live in [config/](config/README.md): system.toml, models.toml, bot.toml and mcp.toml, each with a public example. Actual files are ignored; there is no local subdirectory. One model file selects cloud/local LLMs and API/process-local BGE embeddings, including UUID-owned account overrides. Encrypted credentials and OAuth remain in private data storage. [Configuration guide](docs/implemented/配置目录与模型后端.md).

Engineering review at core4c8025b72f5c: 1,552 tests, zero failures/errors/skips; real local BGE 4/4 without Dense/RRF fallback. This historical engineering receipt predates the new evaluation runner. Full research results and engineering acceptance are reported separately.
