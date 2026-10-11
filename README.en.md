# Memoria-Agent

[中文](README.md) · [Implemented features and plans](docs/README.md) · [AI instructions](AGENTS.md)

A memory management HTTP service for small-model long-term companionship. It provides source-linked review, correction/restoration, validity intervals, hybrid retrieval, token-aware long-document indexing, layered context, independent shared governance, and local/remote OpenAI-compatible model interfaces.

The chatbot frontend has been removed. Use the API, Swagger `/docs`, or experiment tools. Structured Claims, conditional user views and access routing remain research plans. Shared ACL does not yet establish multi-user isolation throughout the personal runtime.

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
.\.venv\Scripts\python.exe scripts/configure_mcp.py
.\.venv\Scripts\python.exe scripts/check_mcp.py
.\.venv\Scripts\python.exe scripts/prepare_real_dialogues.py --limit 50 --import-corpus
.\.venv\Scripts\python.exe -m pytest -q
```

MCP installs into `.mcp/` using project-local npm dependencies and the official GitHub binary. See [MCP and Feishu OAuth](docs/implemented/MCP部署与授权.md). Feishu needs private credentials and browser authorization; installation alone does not establish account access.

The pinned LCCC-base subset contains 50 authentic public Weibo dialogues / 206 utterances, with an isolated corpus DB, source mappings and checksums. Stable speakers, event times and QA labels are unavailable; it cannot establish longitudinal companion quality. Synthetic trajectories, demo servers, old benchmark adapters and score reports have been removed. Engineering test inputs are not research data.

See the [documentation index](docs/README.md) and [GitHub rules](docs/implemented/github提交规范.md). Secrets, weights, databases, raw dialogues and runtime dependencies stay ignored locally.
