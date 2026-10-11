# Memoria-Agent

[English](README.en.md) · [功能说明与研究计划](docs/README.md) · [AI 项目指令](AGENTS.md)

面向小模型长期陪伴的记忆管理服务。核心工作是保存可追溯的来源、审核候选、纠正与恢复版本、理解变化并快速访问证据。当前以 HTTP API 和实验工具为入口，聊天前端已移除；待验证的 Claim、用户视图和路由见 [研究计划](docs/plans/长期陪伴记忆研究与实验计划书.md)。

## 已实现

- 个人记忆的来源定位、审核、去重强化、纠正、撤销、历史有效区间和 Markdown 投影。
- FTS/中文 BM25/Dense/RRF、图扩展与独立模型空间；BGE 长文分块、事务索引和命中片段。
- 工作/情景/语义/程序层与上下文预算、来源清理和情景维护。
- 独立共享空间 ACL、提案审批、CAS 版本治理与来源核对；共享身份尚未贯穿旧个人聊天运行时。
- 本地 Qwen/BGE 与远程 OpenAI 兼容接口、SSE、工具循环、取消和租约 worker。
- 真实 MCP 接入、工具搜索/授权、trace、阶段计时和实验账本。
- 50 段/206 条真实公开微博对话，保存在独立语料库，不充当个人画像或长期陪伴成绩。

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

服务根路径返回 JSON；`/docs` 为 Swagger，`/api/health` 为存活检查。私人库和公开语料库是独立文件，storage 路径决定使用哪个库。

本地模型在 `model/bge-small-zh-v1.5` 和 `model/Qwen3.5-2B`。新机器按 [Embedding 配置](docs/implemented/本地Embedding配置.md)与 [统一模型接口](docs/implemented/本地与远程OpenAI统一接口.md)安装依赖和启动。main/fast 可使用 `Qwen3.5-2B`、`http://127.0.0.1:8080/v1`；embedding 可使用 `bge-small-zh-v1.5` 和同一地址。远程 API 通过同一业务接口配置，不默认发起收费调用。

## 本地 MCP 与真实数据

```powershell
.\.venv\Scripts\python.exe scripts/install_mcp.py
.\.venv\Scripts\python.exe scripts/configure_mcp.py
.\.venv\Scripts\python.exe scripts/check_mcp.py
.\.venv\Scripts\python.exe scripts/prepare_real_dialogues.py --limit 50 --import-corpus
```

安装位置、验证状态与飞书私密输入/OAuth 见 [MCP 部署](docs/implemented/MCP部署与授权.md)。只部署搜索/GitHub/飞书，其他免费服务由用户选择后再部署。

## 开发与验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

工程临时输入和故障替身不导入产品、不作为实验数据。旧模拟轨迹、演示服务、合成基准入口与成绩已移除。模块职责见 [核心目录](memoria/README.md)，提交前读取 [GitHub 规范](docs/implemented/github提交规范.md)。

`config.toml`、`data/`、`.mcp/`、`.venv/` 和 `model/` 被忽略；原始语料由固定版本脚本复现，仓库只保存来源与 SHA 清单。
