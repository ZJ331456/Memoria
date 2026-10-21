# API：登录网关、记忆与治理

memory-answer现复用Kernel.answer；新增memory-kernel/knowledge-answer（仅library）及lineage/{domain}/{id}，来源/候选/选择/版本与Reader引用可关联。全部走原登录网关；[生产闭环与逐题归因](../../docs/implemented/生产记忆闭环与逐题归因.md)说明协议、预算及尚未知的语义效果。

[核心包](../README.md) · [完整接口](../../docs/implemented/API接口文档.md) · [用户隔离](../../docs/implemented/用户登录与数据隔离.md)

正式入口 `main.py` 调用本包导出的 `create_app`。`gateway.py` 验证用户登录后，以服务器账号 UUID 路由到独立租户应用；不要直接暴露 `_create_tenant_app` 作为业务入口。

## 文件映射

| 文件 | 职责 |
| --- | --- |
| [document_import.py](document_import.py) | 账号网关后的二进制文本文件上传、原件下载与OpenAPI binary体说明，不按HTTP请求读取服务器路径 |
| [memory_kernel.py](memory_kernel.py) | `/api/memory-kernel`原生资料保存、按类型读出与有界检索，不要求先发起Chat |
| [library.py](library.py) | `/api/library`修订/历史/反馈/提炼/撤回；创建与核心共用实现 |
| [gateway.py](gateway.py) | 注册/登录/退出、Cookie 或 Bearer 认证、按账号运行时、请求隐私边界、OpenAPI 和 `/studio/` 托管 |
| [app.py](app.py) | 租户组件生命周期、中间件与路由组合；业务路由已拆分 |
| [schemas.py](schemas.py)、[context.py](context.py) | 原HTTP请求/响应契约与明确租户依赖 |
| [conversations.py](conversations.py)、[memories.py](memories.py)、[versions.py](versions.py) | 原会话/SSE/取消、记忆审核/版本/历史接口 |
| [formation.py](formation.py)、[episodes.py](episodes.py)、[integrations.py](integrations.py)、[status.py](status.py) | 形成投影、情节、模型/MCP/工具及状态接口 |
| [studio.py](studio.py) | 只读HTTP投影；SQL在persistence/StudioRepository |
| [claims.py](claims.py) | Claim 原话、候选/修订/明确审核、条件视图、结构化访问、统一查询/回答与分析建议 |
| [governance.py](governance.py) | 账号内 Agent key、共享空间 ACL、提案/审批/版本/撤回及审计 |
| [research_runs.py](research_runs.py) | 当前账号实验 manifest/metrics 只读清单与指纹核验；不启动脚本 |
| [__init__.py](__init__.py) | 导出正式 `create_app` |

## 主要入口

| 路径 | 行为 |
| --- | --- |
| `/`、`/api/health`、`/docs`、`/openapi.json` | 浏览器根地址独立Chat、普通请求JSON导航、存活/Swagger/接口模式；健康不表示模型可用 |
| `/api/auth/register`、`/api/auth/login`、`/api/auth/me`、`/api/auth/logout` | 账号注册、登录会话和退出 |
| `/studio/` | 同源公开静态壳；页面数据仍需登录，未构建时返回 503 |
| `/api/sessions`、`/api/sessions/{id}/chat/stream` | 当前账号会话、聊天和SSE；Chat/飞书可信绑定共用会话锁、取消与Reader |
| `/api/channels/feishu`、`/api/channels/feishu/pairing`、`/api/channels/feishu/binding` | 登录后的本账号状态/绑定码/解绑；不公开应用凭据或对外Webhook |
| `/api/memories`、`/api/memory-reviews`、`/api/messages/{id}/source` | 普通记忆、自动候选审核与原话回查 |
| `/api/claim-slots`、`/api/claim-source/{id}`、`/api/claims` | 12 槽位、来源资格与结构化候选/版本/审核 |
| `/api/claim-views`、`/api/memory-access` | 条件视图及明确槽位/条件/双时间访问 |
| `/api/memory-query`、`/api/claims/{id}/analyze` | 有限中文解析、真实候选诊断/整组选择和明确请求的审核建议 |
| `/api/memory-answer` | 当前账号记忆reader的缓冲回答、引用/来源新鲜度校验；可能调用远程收费模型，缺证据可直接澄清 |
| `/api/memory-layers`、`/api/episodes` | 分层配置、活动情景及管理归档 |
| `/api/shared/*`、`/api/governance/*` | 独立共享记忆治理；不能只凭 Agent key 绕过用户登录 |
| `/api/research-runs` | 当前账号规范运行记录；不返回原始 prompt、gold 或密钥 |
| `/api/tools`、`/api/mcp`、`/api/skills`、`/api/drift`、`/api/traces`、`/metrics` | 扩展状态与诊断；业务和指标需要登录 |

## 权限与生命周期

Cookie 为 HttpOnly/SameSite；网关校验 Origin、跨站请求与分块请求体大小，非回环远程业务要求 HTTPS。模型配置写接口由本机管理员维护，普通注册用户不能把服务密钥重定向至任意地址。配置中 `api_token` 为空不取消正式网关登录。当前租户池最多 32 个驻留账号运行时，未实现多节点调度。

每个租户 lifespan 启停 MCP、候选抽取 worker、情景维护及 Drift；结束任务后再关闭 Store。会话轮次有锁与取消机制。个人记忆、Claim 和共享记忆表/权限分开；工具调试 `confirm_write=true` 不等同于 Claim 或共享提案批准。

Claim 审批带预期修订号并重新核对来源；模型支持度仅是未持久化建议。`/api/memory-query` 不调用 reader、不写会话或自动批准。只读研究记录拒绝跨账号、路径跳转、链接目录与指纹不匹配产物。请求/响应完整字段以 Swagger 和接口文档为准。

## 验证

analyze新增use_relation_model/peer_limit及核验/关系/4类影子策略；extract附带0额外模型调用的shadow_updates。query新增scope_mode区分明确默认与场景。[算法说明](../../docs/implemented/Claim证据核验与演化策略.md)。

在仓库根目录：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_user_privacy.py tests/test_claims_api.py tests/test_memory_studio.py tests/test_governance_api.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_memory_review.py tests/test_mcp_lifecycle.py
```

这些测试验证权限、契约与事务，不证明真实 reader 效果或部署可用性。Studio 构建与使用见 [frontend/](../../frontend/README.md)。

2026-10-13新回答契约及完整请求预算见[实现说明](../../docs/implemented/完整Reader预算与生产引用校验.md)；[test_reader_contract.py](../../tests/test_reader_contract.py)验证新接口登录/Origin/身份参数拒绝。引用通过不标为语义正确，旧memory-query不自动发起reader调用。

本机未提交扩展：登录/me返回服务端role；/api/admin/users及/api/admin/users/{username}/password仅superadmin可用，普通用户403，匿名401。角色只由本机CLI授予，账号内数据仍按UUID隔离，密码修改撤销会话并写无凭据审计。[本机角色说明](../../docs/implemented/用户登录与数据隔离.md#本机qin账号与超级管理员2026-10-14仅本地未提交)。

通用前后台的只读投影见[studio.py](studio.py)：summary、conversations、conversations/{id}/messages、memories，均挂在/api/studio且走当前租户网关。标题/全文字面搜索、来源筛选、页面总量和原话锚点不调用模型；正文保留原content，阅读投影独立。根地址独立Chat与后台/studio/共用Cookie；普通API根JSON保留，/app/已移除。源码和构建已迁至frontend，静态资源从/assets/提供。跨账号ID404，最多100条/页；[详见当前Studio说明](../../docs/implemented/MemoryStudio与统一记忆访问.md#通用前后台与历史资料后台2026-10-14)。

2026-10-16新增POST messages/{id}/memory-extraction与memory-reviews/{id}/auto-review；默认模型主审、人工辅助，审核记录含assessment。原只读memory-query/analyze行为不变。[写入政策与接口](../../docs/implemented/LLM自动审核与人工辅助.md)。

生产记忆入口必须有Kernel，schema_only仅用于OpenAPI且记忆请求503。API/渠道/公开账号模块不直接执行SQL；68个原租户HTTP契约有冻结检查。见[统一入口](../../docs/implemented/统一内核入口与存储边界.md)。
