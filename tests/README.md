# 工程测试导航

[项目首页](../README.md) · [核心模块](../memoria/README.md) · [真实研究](../memoria/experiments/README.md)

临时输入、隔离账号/数据库和模型/传输故障替身只验证工程行为，不写入生产、不作为真实用户数据或研究成绩。正式效果结果由真实来源、冻结协议和独立评分记录说明。

## 运行

从仓库根目录使用 `.venv`。新开发环境先在该环境安装 pytest；它们是开发测试依赖，不使用 base 或全局 Python。

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest -q
# 受影响范围示例
.\.venv\Scripts\python.exe -m pytest -q tests/test_user_privacy.py tests/test_claims.py tests/test_memory_studio.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_research_workflow.py tests/test_source_retrieval.py
# 前端测试单独在 studio 操作
Push-Location studio
npm ci
npm test
npm run build
Pop-Location
```

纯文档更新优先验证路径/命令/Markdown，不把历史测试输出写为本轮新回归。库、模型和网络能力改变时按对应模块执行测试，真实外部服务验证需另行进行。

## 按能力定位

| 测试文件 | 重点 |
| --- | --- |
| [test_core.py](test_core.py) | CRUD、版本/去重/语义回填、工具循环、预算、生命周期和 trace |
| [test_optimizations.py](test_optimizations.py) | 持久任务/租约、FTS、查询门控、工具授权、SSE、内部安全和可选向量 |
| [test_user_privacy.py](test_user_privacy.py) | 注册/登录/重设失效、每账号路由/工具、CSRF、HTTPS、流式体积与私密文件 |
| [test_claims.py](test_claims.py) | 来源/公开拒绝、审批OCC、不可变修订、双时间/条件/冲突、依赖缓存及事务/竞态 |
| [test_claims_api.py](test_claims_api.py) | 两账号网关、Claim 契约、有限个人问题和原子读取预算 |
| [test_memory_studio.py](test_memory_studio.py) | 时间/主体歧义、统一补检来源/版本、固定池预算、只读建议、账本owner/SHA和HTTP隔离 |
| [test_memory_review.py](test_memory_review.py) | 自动提取待审、批准后召回、来源定位、重启恢复 |
| [test_memory_retrieval_scale.py](test_memory_retrieval_scale.py) | 较早/低重要度召回、版本过滤、问句尾词和无事实查询 |
| [test_memory_consistency.py](test_memory_consistency.py) | 版本回滚、撤销多区间、历史融合/图、来源/并发与模型切换 |
| [test_review_source_transactions.py](test_review_source_transactions.py) | 来源失效、跨连接删除竞态和外层回滚 |
| [test_document_memory.py](test_document_memory.py) | 长记忆分块索引、命中片段、父版本与重索引一致性 |
| [test_token_chunks.py](test_token_chunks.py) | tokenizer 分块、原文连续覆盖和边界 |
| [test_embedding_client.py](test_embedding_client.py) | 批次限额、排序/完整覆盖及无效向量拒绝 |
| [test_local_embedding.py](test_local_embedding.py) | 本地 BGE 指令/模型空间、设备与编码替身 |
| [test_llm_backends.py](test_llm_backends.py) | 后端路由/公开标签、HTTP与本地服务契约 |
| [test_local_llm_slots.py](test_local_llm_slots.py) | 免Key回环/local readiness、角色/覆盖、Qwen工具与模板参数 |
| [test_openai_compat.py](test_openai_compat.py) | 严格本地接口、SSE/usage/错误、工具/Thinking/截断与取消 |
| [test_episodic_memory.py](test_episodic_memory.py) | 来源任务、TTL/固定/归档、预览与级联删除 |
| [test_layered_context_budget.py](test_layered_context_budget.py) | 分区/整帧、伪造标题、超长输入与完整工具协议 |
| [test_memory_layers_integration.py](test_memory_layers_integration.py) | 分层运行时、情景/技能trace、取消/删除、审批竞态与紧急预算 |
| [test_layer_settings_config.py](test_layer_settings_config.py) | 配置继承/严格字段、技能完整文件指纹 |
| [test_incremental_compaction.py](test_incremental_compaction.py) | 500+历史前缀、partial续读、覆盖/CAS/限额/来源失效 |
| [test_governance_core.py](test_governance_core.py) | Agent key、ACL、冲突、来源、失权和并发审批 |
| [test_governance_consistency.py](test_governance_consistency.py) | 提交/批准来源重验和授权范围内BM25/Top K |
| [test_governance_api.py](test_governance_api.py) | 用户/管理员/Agent边界、共享版本/撤销及导入 |
| [test_store_transactions.py](test_store_transactions.py) | 普通记忆元数据不得提前提交治理外层事务 |
| [test_dataset_import.py](test_dataset_import.py) | 真实来源清单、导入映射/幂等/回滚与边界 |
| [test_research_workflow.py](test_research_workflow.py) | 来源同意、人物划分、AI/真人审核、冻结/前缀/gold隔离、失败分母/恢复/评分 |
| [test_source_retrieval.py](test_source_retrieval.py) | 未来缓存不泄漏、原文ID/分块/向量空间、Dense/RRF |
| [test_experiment_records.py](test_experiment_records.py) | 角色/账本、SHA、实际阶段及未执行/失败记录 |
| [test_skills.py](test_skills.py) | 一级技能发现/匹配、API/加载和HTTP初始主机 |
| [test_mcp_lifecycle.py](test_mcp_lifecycle.py) | stdio分页/ping/超时/清理/命名冲突和服务入口 |
| [test_round7_akashic.py](test_round7_akashic.py) | 帧、压缩、事件、并行工具、中断和Markdown |
| [test_round9_setup_markdown.py](test_round9_setup_markdown.py) | 模型设置覆盖、内部Setup和Markdown/SELF |
| [test_round10_tool_search_mcp.py](test_round10_tool_search_mcp.py) | 按需工具schema与MCP命名 |
| [test_round11_drift.py](test_round11_drift.py) | Drift开关/跳过/限额/接续与技能 |

`round*` 保留文件演进名称，按职责定位即可。[retrieval_metrics.py](retrieval_metrics.py) 是指标辅助，不是待收集的测试文件。

## 外部与研究边界

[test_claim_evolution.py](test_claim_evolution.py)新增27项独立核验/关系/策略、源时间、主体/计划/否定、并发目标、模型引用、无提交和默认缓存反例；项目.venv执行`-m pytest -q tests/test_claim_evolution.py`。临时输入不作为研究数据或质量分数。

- sqlite-vec 可选，未安装时相应用例可跳过；在项目环境安装 requirements-vector.txt 后再验证该后端。
- 传输/模型替身不证明真实 Qwen/BGE 质量；网络握手不证明业务权限或可靠性。MCP 真实只读验证用 `scripts/check_mcp.py --user USERNAME`，飞书先完成 OAuth。
- 跨账号测试不等于任意个人信息脱敏、全盘加密、多节点隔离或商用安全认证。
- 没有把合成轨迹/演示画像导入产品或报为研究成绩。实际50匿名对话没有连续人物时间，不能由本目录推出长期陪伴结论。

## 已有验收证据

2026-10-12 Memory Studio 实现交付已记录全量 **405 passed / 91.20 s**、前端 **3 项**、vue-tsc/生产构建及隔离真实HTTP链路通过；这是此前实现的验收，不是本次 README 更新重新跑出的结果。以后实际数量/跳过项以当次输出为准，模块状态与局限见 [实现说明](../docs/implemented/MemoryStudio与统一记忆访问.md)。
