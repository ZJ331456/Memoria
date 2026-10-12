# Memoria-Agent

[中文完整版](README.md) · [Documentation](docs/README.md) · [Research and commercial-readiness plan](docs/plans/长期陪伴记忆研究与实验计划书.md)

Source-backed memory management for small-model companionship: reviewed Claims, conditions, dual-time revisions, source retrieval, dependent views, bounded evidence and account-isolated execution. Memory Studio provides source review, timelines, retrieval diagnostics and read-only run records. The current scope is a local research prototype and controlled-pilot foundation; public commercial readiness and longitudinal quality remain unverified.

## Local setup and startup

Run from the repository root in Windows PowerShell. Preserve an existing private configuration and use the project virtual environment.

```powershell
if (-not (Test-Path .venv\Scripts\python.exe)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path config.toml)) { Copy-Item config.example.toml config.toml }
Push-Location studio
npm ci --cache .npm-cache
npm run build
Pop-Location
.\.venv\Scripts\python.exe main.py --config config.toml --host 127.0.0.1 --port 2237
```

Open `http://127.0.0.1:2237/studio/`; `/docs` exposes the API contracts and `/api/health` checks process liveness. Studio must be built before starting/restarting the service. It currently offers login, without a signup page:

```powershell
.\.venv\Scripts\python.exe scripts/manage_users.py reserve your_username
.\.venv\Scripts\python.exe scripts/manage_users.py set-password your_username
```

Password entry is hidden and resets revoke sessions. The existing local qin account is already configured. Empty api_token never bypasses the formal login gateway; each server-owned UUID selects its own database, files, workers and MCP configuration.

Choose either remote main/fast with in-process local BGE (`local://auto`), or the local HTTP Qwen/BGE server. The [Chinese startup guide](README.md#3-模型方案选择一种) includes optional dependencies, protected Key input, private-override precedence and both complete configurations. Weights are ignored and must already exist locally; the BGE download script verifies a pinned public snapshot.

```powershell
# Separate terminal, only when choosing local HTTP inference
.\.venv\Scripts\python.exe scripts/serve_local_llm.py --model model/Qwen3.5-2B --embedding-model model/bge-small-zh-v1.5 --device cuda --port 8080 --max-context-tokens 8192 --warmup
```

MCP and source import are optional, not part of the minimal startup. See [scripts](scripts/README.md), [Studio](studio/README.md), [MCP](docs/implemented/MCP部署与授权.md) and [data provenance](datasets/README.md). No default credentials or synthetic production profiles are included.

## Registered historical development results

Measured on clean commit 040427c, 2026-10-12: 12 retrieval runs / 768 query runs and 9 QA runs / 576 DeepSeek calls; 50 anonymous source groups, 206 utterances, 64 queries (41 answerable / 23 unanswerable), three repetitions per condition. AI-assisted development gold; literal match is not independent human semantic accuracy. These results do not evaluate later Studio or selectors; the older six-run compact QA batch is separate.

![Known-source recall](docs/assets/evaluation/source-recall.png)

| Method | Known evidence recall | Empty context | Retrieval p50/p95 ms | QA literal match |
| --- | --- | --- | --- | --- |
| Window | 123/123, 100% | 0/192 | 0.003/0.003 | 166/192, 86.46% |
| Compact FTS | 45/123, 36.59% | 81/192 | 0.537/0.676 | Not run in this batch |
| Dense | 123/123, 100% | 0/192 | 11.433/38.011 | 161/192, 83.85% |
| RRF | 123/123, 100% | 0/192 | 12.062/18.403 | 158/192, 82.29% |

![Literal QA and citation recall](docs/assets/evaluation/qa-literal-and-citations.png)

Dense/RRF fill the compact recall gap; Window also covers all known evidence. The new methods did not exceed Window on this batch's literal metric. Failures 3/5/7 remain in each 192-run denominator: 13 unseen-citation and 2 provider failures. Usage is known for 574/576 calls, totaling 353,502 known tokens; monetary cost is unknown.

![Empty-state overhead](docs/assets/evaluation/empty-claim-access.png)

The access benchmark used a consistent real-account database copy with 0 approved Claims and 1,800 calls per condition; every response was `clarify`. Empty cache added overhead; positive-profile benefits and HTTP/reader SLOs were not measured. Full methods, maintenance costs and limitations are in the [report](docs/implemented/DenseRRF自动评测与视图性能.md), [registration](configs/research-retrieval-eval-20261012.json) and [figure provenance](docs/assets/evaluation/README.md).

```powershell
# Only public aggregates, no inference or new scoring
.\.venv\Scripts\python.exe -m pip install -r requirements-docs.txt
.\.venv\Scripts\python.exe scripts/render_eval_charts.py
.\.venv\Scripts\python.exe scripts/render_eval_charts.py --check
```

## Next work and commercial readiness

Prioritize production backup/verify/restore, a consented personal-source intake, exact reader token accounting and citation constraints; then durable index cursors, atomic projections, general conditions and newly registered selector diagnostics. Genuine longitudinal B3/B5/B6 studies need new authorized continuous personal histories; independent human evaluation remains deferred.

Per-user SQLite/WAL is the current storage design. Complete account export/deletion, backup retention and restore policy, versioned migrations, resource/cost quotas, SSRF-safe outbound requests, HTTPS trusted-proxy deployment, tenant lifecycle reclamation, readiness/histograms/alerts, reproducible CI/releases and license inventories remain pending. Database encryption and cross-machine credential migration are distinct from NTFS/DPAPI. The plan defines single-host and later scaling gates; PostgreSQL or Kubernetes are not prerequisites invented for this prototype.

[Module documentation](memoria/README.md) · [Tests](tests/README.md) · [AI instructions](AGENTS.md) · [GitHub conventions](docs/implemented/github提交规范.md). Historical engineering baseline: 405 tests / 91.20 s plus 3 frontend tests and build, not research quality. Secrets, weights, raw histories, gold and runtime dependencies stay out of Git; old personal history remains unimported.
