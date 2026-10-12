# Memoria 核心包

[项目首页](../README.md) · [文档导航](../docs/README.md) · [系统架构](../docs/implemented/系统架构与实现说明.md) · [研究计划](../docs/plans/长期陪伴记忆研究与实验计划书.md)

此目录实现账号隔离的记忆 HTTP 服务。正式入口是 `main.py → api.create_app`；网关登录后以服务器生成的 UUID 选择独立数据库、运行时、Markdown 和 MCP 配置。Memory Studio 调用同一网关，不另建数据源。

## 模块导航

| 目录 | 职责与入口 |
| --- | --- |
| [api/](api/README.md) | 登录网关、个人记忆与 Claim、共享治理、SSE、只读实验账本和 Studio 静态托管 |
| [memory/](memory/README.md) | 普通记忆版本、长文分块、混合召回、统一访问、中文时间、整组证据选择及审核辅助 |
| [llm/](llm/README.md) | 本地与远程 OpenAI 兼容客户端及本地文本/Embedding 服务 |
| [runtime/](runtime/README.md) | 一轮对话、有限个人问题的统一访问、工具循环、压缩、取消与 trace |
| [experiments/](experiments/README.md) | 真实来源、标注冻结、前缀隔离 runner、独立评分和实际账号副本基准 |
| [prompting/](prompting/README.md) | 低信任上下文帧、分区字符预算、工具协议与原子状态保护 |
| [tools/](tools/README.md) | 工具注册、按需发现、参数校验、授权、调用次数和输出限额 |
| [mcp/](mcp/README.md) | 每账号外部 stdio MCP 生命周期与工具接入 |
| [skills/](skills/README.md) | 扫描和加载项目 `skills/*/SKILL.md`，不执行技能脚本 |
| [drift/](drift/README.md) | 默认关闭的限额空闲整理轮次 |
| [lifecycle/](lifecycle/README.md) | 五个对话扩展阶段及处理器顺序 |
| [observability/](observability/README.md) | 请求上下文、事件、trace、实际阶段耗时和 Prometheus 文本 |

## 根级文件

| 文件 | 职责 |
| --- | --- |
| [config.py](config.py)、[models_config.py](models_config.py) | 配置加载、角色选择和管理员模型覆盖；公开返回不包含密钥 |
| [accounts.py](accounts.py)、[private_files.py](private_files.py) | 密码散列、登录令牌、账号目录和本机私密 JSON 保护 |
| [service.py](service.py) | 组装账号内 Store、LLM、记忆、Claim、runtime 与后台 worker |
| [store.py](store.py)、[vector_index.py](vector_index.py) | SQLite、FTS、来源/有效区间、持久任务及可选 sqlite-vec |
| [claims.py](claims.py) | 12 槽位 Claim、原话 span/SHA、明确审核、不可变修订、条件视图及双时间访问 |
| [governance.py](governance.py) | 独立共享空间、Agent key、ACL、提案、审批、CAS 和审计 |
| [security.py](security.py) | 内部路由的 Origin、请求体、可选服务 Token 与速率限制；不替代正式网关登录 |
| [local_llm.py](local_llm.py) | 旧导入兼容入口；新模型执行代码位于 `llm/backends/process_engine.py` |

## 数据与访问边界

普通 `memories`、结构化 Claim 和共享空间使用不同表和治理规则。Claim 不投影为普通记忆或向量；来源补检也不能把未批准原话变成已批准画像。共享空间需要登录及账号内的 Agent key/ACL，尚未自动注入个人对话运行时。

`MemoryAccessOrchestrator` 将已批准 Claim、有限中文时间区间、合法普通记忆来源和字符预算选择连接到 `/api/memory-query`。少量个人状态问题复用该路径，一般任务保持既有规划流程；API 只读诊断不调用 reader。详情见 [Claim](../docs/implemented/Claim条件视图与访问路由.md)与 [Studio/统一访问](../docs/implemented/MemoryStudio与统一记忆访问.md)。

SQLite 与文件隔离已经实现，不等于商用多节点数据库、备份恢复、完整数据加密或服务 SLA。公开对话归属实验空间，不代表账号本人事实；长期质量和商用缺口见研究计划。

## 验证

在仓库根目录使用项目 `.venv`：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_user_privacy.py tests/test_claims.py tests/test_claims_api.py tests/test_memory_studio.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_memory_consistency.py tests/test_governance_core.py tests/test_governance_api.py
```

完整测试导航见 [tests/](../tests/README.md)。临时输入只验证工程边界，不写入实际账号，也不作为长期陪伴成绩。
