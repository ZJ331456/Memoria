# 项目记忆 DevMemory 与任务闭环（2026-10-27）

在核心算法冻结的前提下拓展业务的第一步：把已实现的"来源入库—版本修订—任务反馈—经验提炼—词面检索"能力组织成**项目工作区**，服务 AI 编程助手与项目跟随场景。当天交付项目注册、Git 历史导入、任务前召回、任务后记录与反馈、Studio 面板、Agent 工具和对外 MCP server，形成完整业务闭环。核心域（`memoria/memory/`）零代码改动；唯一冻结面变更是租户契约清单按设计登记新增路由（见验收节）。

## 定位

Memoria 不再只是聊天记忆：一个项目（代码仓库）的提交历史、设计决策、任务结果与失败经验进入同一账号的证据库，并按项目组织。典型循环：

1. 导入：Git 提交历史导出为文档来源，或粘贴/上传设计文档。
2. 任务前召回：在项目内检索"为什么用这个方案 / 上次怎么修的 / 哪些尝试已失败"。
3. 任务后记录：结果写成 `task_result` 来源并强制附验收反馈（success/failure/unknown）。
4. 经验提炼：对单条任务结果显式触发 LLM 提炼，产出引用原文与反馈的来源约束观察。

## 架构与边界

- 业务层 `memoria/projects/`：`ProjectWorkspaces`（注册表、来源绑定、任务、事件）与 `git_source.collect_git_history`（只读 `git log` 导出，绝不写仓库）。不在 `memoria/memory/` 内新增任何代码。
- 数据：账号库内新增 `project_workspaces`、`project_sources`、`project_tasks`、`project_events` 四张表。SQL 只触这四张表，统一走 `Store.memory_transaction()`；来源及其版本/反馈仍在 library 领域表内。
- 来源语义：项目来源仍是 `note/document/task_result` 三类之一，`scope='personal'`、不绑定会话；`session_id` 引用真实聊天会话的外键语义未被借用，项目归属只存于 `project_sources` 映射。项目不是会话，也不生成个人 Claim。
- Kernel 门面：入库 `kernel.ingest/ingest_file`、反馈 `kernel.source_feedback`、检索 `kernel.search_sources`（词面 FTS，跨个人来源检索后按项目成员过滤）、提炼 `kernel.distill_source`。业务层不执行任何对领域表的 SQL。
- 时间边界：Git 导出文本自带每条提交时间并在文中声明"导出时间不补齐为事件时间"，来源不填 `source_time`；任务结果的 `source_time` 为结果记录时刻。撤回的来源保留绑定快照但标注 `unavailable`。
- 模型调用：导入、检索、任务记录零调用；唯一 LLM 入口是显式的单任务提炼（沿用 library 现有提炼与支持核验）。反馈口径不变：流程完成不代表任务成功，success/failure 都不证明普遍因果。
- 隔离：路由挂在租户 app（`/api/projects/*`），数据落在 `data/users/<uuid>/` 账号库；网关与登录态不变。

## 接口

| 入口 | 说明 |
| --- | --- |
| `POST/GET /api/projects` | 创建（名称唯一）、列出项目 |
| `GET/DELETE /api/projects/{pid}` | 详情（来源、任务、事件、来源存活状态）、归档 |
| `POST …/import/git` | 按仓库路径导出提交历史为文档来源（`since`/`max_commits`/`include_body`），origin 记 `git-log:<repo>@<head前12位>` |
| `POST …/import/text`、`POST …/import/file` | 粘贴文本或上传文件（复用 document_parser，1 MiB 上限） |
| `POST …/tasks`、`POST …/tasks/{tid}/finish` | 任务登记；结果必须写 outcome 与内容，来源+反馈+关单同批完成 |
| `POST …/tasks/{tid}/distill` | 显式单任务经验提炼（调用模型） |
| `POST …/recall` | 项目内检索：命中、检索总量、开放任务一并返回 |

Agent 工具（`memoria/tools/projects.py`，随 `build_registry` 注册）：`project_list`、`project_recall`、`project_record_task`（写风险，需 turn 授权）。Chat 与机器人 Agent 因此能在对话中列出项目、任务前召回、任务后记录。

对外 MCP server：`scripts/devmemory_mcp_server.py`（stdio，协议 2024-11-05），环境变量 `MEMORIA_URL`、`MEMORIA_USERNAME`、`MEMORIA_PASSWORD`（可选 `MEMORIA_PROJECT` 默认项目）。正式登录换取 Bearer token，全部业务走 HTTP API 进入租户 Kernel，不直接访问数据库。工具：`list_projects`、`project_context`、`recall_project_memory`、`record_task_result`。Cursor / Claude Code 等编程 Agent 以此接入：

```json
{"mcpServers": {"memoria-devmemory": {
  "command": "<项目>/.venv/Scripts/python.exe",
  "args": ["<项目>/scripts/devmemory_mcp_server.py"],
  "env": {"MEMORIA_URL": "http://127.0.0.1:2237", "MEMORIA_USERNAME": "<账号>", "MEMORIA_PASSWORD": "<密码>"}
}}}
```

前端：Studio 新增「项目记忆」页（`frontend/studio/src/components/ProjectMemory.vue`，哈希路由 `#projects`），覆盖创建/导入/任务循环/检索/归档全流程。

## 验收（工程，2026-10-27）

- `tests/test_project_memory.py` 17 项通过：Git 导出内容与 provenance、`since`/截断边界、非仓库与空仓库拒绝、项目查重/归档/解析、任务反馈闭环与重复关单拒绝、项目间召回过滤、提炼守卫（无结果 409、无 LLM 503，业务层不自动调用模型）、租户路由全矩阵与 schema_only 声明、Agent 工具注册与执行、MCP 协议帧（initialize/通知/tools/list/未知方法/未知工具/错误即结果）。
- **冻结契约登记**：新增 11 条 `/api/projects` operation 与 6 个请求模型进入 `tests/fixtures/tenant_routes_v2.json`（141→152）；生成前逐项 diff 确认只有新增、零删改，全部带 `require_kernel`+`require_projects` 双守卫。这是本轮唯一的核心指纹变更（tests/fixtures）。
- 完整回归：契约更新后 `.venv` 全量 `pytest tests` 零失败零错误；本地实际 BGE offline-retrieval 通过（Keyword/BM25/Dense/RRF 四路全部召回目标，Dense/RRF `dense_available=true` 实调 Embedding，`model_calls=0`）；前端 `vue-tsc`+`vite build` 成功、`npm test` 22 项通过。
- MCP 真实初始化冒烟（本机临时库、独立账号、2299 端口，非生产数据）：真实网关注册/登录 → 创建项目并导入本仓库 30 条提交（head 18e5899）→ MCP 子进程 initialize/tools/list → `recall_project_memory` 真实召回 1 条 → `record_task_result` 写入任务结果且反馈 outcome=success 可查。连接失败、409 冲突等失败路径在冒烟中也实际出现过并按预期报错。
- **未闭环项**：三重门槛中的"同指纹完整 simulation 重执行"本轮未跑（需真实 DeepSeek 调用付费，且预算执行须用户显式授权）。在获得授权并重跑通过前，本轮**不宣称核心功能验收通过**；工程与本地诊断通过不等于长期使用效果或检索质量收益。
- **本地模型冒烟（用户指定，2026-10-27）**：qin 槽位临时切换本地 Qwen3.5-2B（CUDA 回环，0 远程调用，54 次模型调用），完整 38 场景：10/49 通过、24 能力缺口、15 前缀执行失败，0 案例级崩溃、0 预算阻断，耗时约 25 分钟。逐项归因：能力缺口为 2B 未形成批准记忆后 Reader 按设计 `clarify`（不编造），执行失败为 2B 抽取输出契约违规触发 `formation_execution_failure`（与 DeepSeek 偶发契约失败同机制，仅频率更高）；**无任何失败指向新增业务代码**。运行后配置已字节级恢复备份。本次为本地弱模型工程链路验证，`acceptance_passed=false` 如实记录，不作为三重验收、不出任何质量分数。

## 未做与下一步

- 项目召回目前是个人域词面 FTS + 项目成员过滤；未接入 Dense/RRF 与项目级预算，检索质量未做对照评估。
- 尚无 GitHub Issues/PR、任务系统等更多适配器；Git 导入为用户显式触发，无自动批量重处理。
- MCP server 仅 stdio 单账号；无多用户托管、无项目级权限模板。
- Studio 面板未做来源原文深链与跨项目聚合视图。
