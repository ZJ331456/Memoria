# Memoria-Agent

[English](README.en.md) · [功能说明与研究计划](docs/README.md) · [AI 项目指令](AGENTS.md)

面向小模型长期陪伴的记忆管理服务。核心工作是保存可追溯的来源、审核候选、纠正与恢复版本、理解变化并快速访问证据。入口为 HTTP API、实验工具与专用 [Memory Studio](docs/implemented/MemoryStudio与统一记忆访问.md)，旧聊天前端仍移除。限定Claim、条件视图与统一访问已实现，研究质量与长期效果待测项见 [研究计划](docs/plans/长期陪伴记忆研究与实验计划书.md)。

## 已实现

- 个人记忆的来源定位、审核、去重强化、纠正、撤销、历史有效区间和 Markdown 投影。
- FTS/中文 BM25/Dense/RRF、图扩展与独立模型空间；BGE 长文分块、事务索引和命中片段。
- 工作/情景/语义/程序层与上下文预算、来源清理和情景维护。
- 独立共享空间 ACL、提案审批、CAS 版本治理与来源核对；共享身份尚未贯穿旧个人聊天运行时。
- 本地 Qwen/BGE 与远程 OpenAI 兼容接口、SSE、工具循环、取消和租约 worker。
- 真实 MCP 接入、工具搜索/授权、trace、阶段计时和实验账本。
- 注册登录、按账号独立数据库/Markdown/来源/后台任务和 MCP；旧个人历史单独封存。
- qin 空间内 50 段/206 条真实公开微博对话，保留未知说话者，不充当个人画像或长期陪伴成绩。
- 有证据的12槽位Claim、条件视图、双时间修订、统一访问/中文区间解析、限定条件提取与待审支持度建议。
- Memory Studio来源审核、变化查看、检索解释、只读执行账本及整组字符预算选择；字符预算不是reader token。

能力和限制见 [架构](docs/implemented/系统架构与实现说明.md)、[API](docs/implemented/API接口文档.md)和 [真实数据](docs/implemented/真实对话数据与实验入口.md)。

## 本机启动

在仓库根目录执行，使用项目虚拟环境，不污染 conda base：

```powershell
# 已有 .venv 时不重复创建
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# 仅首次安装复制模板；已有 config.toml 不要覆盖
Copy-Item config.example.toml config.toml
.\.venv\Scripts\python.exe main.py --host 127.0.0.1 --port 2237
```

服务根路径返回JSON导航；专用工作室位于`/studio/`（先在studio执行npm ci与npm run build）；`/docs`为Swagger，`/api/health` 为存活检查。先注册/登录，业务请求使用用户 Bearer 或 Cookie；所有业务库按账号 UUID 隔离。本机 qin 已预留且用户已私密设置密码；新安装或重设时用 `scripts/manage_users.py set-password qin` 隐藏输入，凭据不写入文档。目录整理、文件保护和登录步骤见 [用户隔离](docs/implemented/用户登录与数据隔离.md)。

本地模型在 `model/bge-small-zh-v1.5` 和 `model/Qwen3.5-2B`。新机器按 [Embedding 配置](docs/implemented/本地Embedding配置.md)与 [统一模型接口](docs/implemented/本地与远程OpenAI统一接口.md)安装依赖和启动。main/fast 可使用 `Qwen3.5-2B`、`http://127.0.0.1:8080/v1`；embedding 可使用 `bge-small-zh-v1.5` 和同一地址。远程 API 通过同一业务接口配置，不默认发起收费调用。

## 本地 MCP 与真实数据

```powershell
.\.venv\Scripts\python.exe scripts/install_mcp.py
.\.venv\Scripts\python.exe scripts/manage_users.py reserve qin
.\scripts\protect_local_data.ps1
.\.venv\Scripts\python.exe scripts/configure_mcp.py --user qin
.\.venv\Scripts\python.exe scripts/check_mcp.py --user qin
.\.venv\Scripts\python.exe scripts/prepare_real_dialogues.py --limit 50 --user qin
```

安装位置、验证状态与飞书私密输入/OAuth 见 [MCP 部署](docs/implemented/MCP部署与授权.md)。只部署搜索/GitHub/飞书，其他免费服务由用户选择后再部署。

## 开发与验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

工程临时输入和故障替身不导入产品、不作为实验数据。旧模拟轨迹、演示服务、合成基准入口与成绩已移除。模块职责见 [核心目录](memoria/README.md)，提交前读取 [GitHub 规范](docs/implemented/github提交规范.md)。

`config.toml`、`data/`、`.mcp/`、`.venv/` 和 `model/` 被忽略；原始语料由固定版本脚本复现，仓库只保存来源与 SHA 清单。

真实历史、人工标注与评测入口为 `scripts/research.py --user qin --help`，见 [研究工具](docs/implemented/真实历史标注与评测.md)。本机已接入 50 段真实公开来源；qin main/fast 已切到独立私密 DeepSeek profile，本地 BGE 保留。公开来源人物/事件时间未知，候选与人工 gold 不自动成为 qin 个人事实。
