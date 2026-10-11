# Memoria-Agent

[中文](README.md) · [Implemented features and plans](docs/README.md) · [AI instructions](AGENTS.md)

A memory management HTTP service for small-model long-term companionship. It provides source-linked review, correction/restoration, validity intervals, hybrid retrieval, token-aware long-document indexing, layered context, independent shared governance, and local/remote OpenAI-compatible model interfaces.

The chatbot frontend has been removed. Use the API, Swagger `/docs`, or experiment tools. Login selects an independent database, memory runtime, Markdown directory and MCP profile per account. Structured Claims, conditional user views and access routing remain research plans.

## Run from the repository root

Use a project virtual environment; keep conda base unchanged:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# Copy only on a new installation; preserve an existing private config.
Copy-Item config.example.toml config.toml
.\.venv\Scripts\python.exe main.py --host 127.0.0.1 --port 2237
```

For local BGE/Qwen, follow the [model guide](docs/implemented/本地与远程OpenAI统一接口.md). Weights live in `model/`; the unified local model server runs on port 8080.

```powershell
.\.venv\Scripts\python.exe scripts/install_mcp.py
.\.venv\Scripts\python.exe scripts/manage_users.py reserve qin
.\scripts\protect_local_data.ps1
.\.venv\Scripts\python.exe scripts/manage_users.py set-password qin
.\.venv\Scripts\python.exe scripts/configure_mcp.py --user qin
.\.venv\Scripts\python.exe scripts/check_mcp.py --user qin
.\.venv\Scripts\python.exe scripts/prepare_real_dialogues.py --limit 50 --user qin
.\.venv\Scripts\python.exe -m pytest -q
```

MCP installs into `.mcp/` using project-local npm dependencies and the official GitHub binary. See [MCP and Feishu OAuth](docs/implemented/MCP部署与授权.md). Feishu needs private credentials and browser authorization; installation alone does not establish account access.

The pinned LCCC-base subset contains 50 authentic public Weibo dialogues / 206 utterances in qin's account, with source mappings and checksums. Ownership does not assert authorship. Stable speakers, event times and QA labels are unavailable; it cannot establish longitudinal companion quality. Old personal history remains archived and unimported. Synthetic trajectories, demo servers, old benchmark adapters and score reports have been removed. Engineering test inputs are not research data. See the [login and privacy guide](docs/implemented/用户登录与数据隔离.md); qin is reserved and its password has been privately configured on this machine; new installations must set their own password.

See the [documentation index](docs/README.md) and [GitHub rules](docs/implemented/github提交规范.md). Secrets, weights, databases, raw dialogues and runtime dependencies stay ignored locally.

Real-history import, human annotation/adjudication, source-only inference and separate scoring are available through `scripts/research.py --user qin --help`; see the [research workflow](docs/implemented/真实历史标注与评测.md). This machine uses an encrypted account-specific DeepSeek profile for qin main/fast and keeps BGE local. The 50 public dialogues remain anonymous development sources, not qin personal facts or a longitudinal benchmark.
