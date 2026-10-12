# 工程测试导航

单元测试的临时输入和故障替身用于验证边界、事务与失败恢复，不作为用户数据或科研成绩。真实语料及实验边界见 [说明](../docs/implemented/真实对话数据与实验入口.md)。

## 运行

在仓库根目录使用项目虚拟环境：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q tests/test_memory_review.py tests/test_memory_consistency.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_mcp_lifecycle.py tests/test_round10_tool_search_mcp.py
```

## 按能力查找

| 文件 | 覆盖内容 |
| --- | --- |
| [test_core.py](test_core.py) | 会话/记忆 CRUD、用户纠正、版本链、重复抑制、语义回填、生命周期顺序、上下文预算、工具循环、错误分类与 trace 脱敏 |
| [test_optimizations.py](test_optimizations.py) | 持久化抽取任务、租约恢复、撤销、FTS、查询门控、工具权限、SSE、API 认证/Origin/请求体限制及可选向量后端 |
| [test_memory_review.py](test_memory_review.py) | 自动提取先待审、批准后召回、旧消息来源定位和处理中候选重启恢复 |
| [test_memory_retrieval_scale.py](test_memory_retrieval_scale.py) | 较早低重要度记忆召回、替代版本过滤、问句尾词和无事实线索查询 |
| [test_memory_consistency.py](test_memory_consistency.py) | 原子版本回滚、撤销多区间、UTC 时间、历史融合、图重排、并发强化、本地来源验证、向量模型/维度迁移与热切换 |
| [test_episodic_memory.py](test_episodic_memory.py) | 情景来源校验、幂等、相关性、TTL/固定保留、管理归档、维护预览和级联删除 |
| [test_layered_context_budget.py](test_layered_context_budget.py) | 分区/整帧限额、伪造标题边界、超长用户/系统、工具协议和并发读取配额 |
| [test_incremental_compaction.py](test_incremental_compaction.py) | 500 条以上历史、partial 续读、真实覆盖、CAS 并发、摘要限额与来源失效清理 |
| [test_memory_layers_integration.py](test_memory_layers_integration.py) | 运行时召回/技能 trace、失败/取消、API 认证/开关、删除活跃会话、来源审批竞态及紧急预算 |
| [test_layer_settings_config.py](test_layer_settings_config.py) | 分层配置继承、严格字段类型和完整技能文件的版本指纹 |
| [test_review_source_transactions.py](test_review_source_transactions.py) | 来源失效时拒绝创建/替代/强化，跨连接删除竞态与外层事务回滚 |
| [test_embedding_client.py](test_embedding_client.py) | 供应商十条批量限制、完整覆盖、向量排序及重复序号/非有限数值/零向量拒绝 |
| [test_governance_core.py](test_governance_core.py) | Agent 密钥、空间 ACL、冲突比较、生命周期、来源校验、密钥恢复、失权提案及跨连接并发审批 |
| [test_governance_consistency.py](test_governance_consistency.py) | 提交/批准时重验本地来源，授权候选范围内的 BM25 与 Top K |
| [test_governance_api.py](test_governance_api.py) | 管理员与 Agent key 认证边界、共享审核/撤销/版本以及旧库导入 |
| [test_store_transactions.py](test_store_transactions.py) | 个人记忆元数据写入不得提前提交共享连接上的治理事务；创建与替代两个分支 |
| [test_round7_akashic.py](test_round7_akashic.py) | Context Frame、会话压缩、事件上下文、工具并行、中断记录与 Markdown 导出 |
| [test_skills.py](test_skills.py) | 技能扫描、触发词匹配、加载 API、内置目录和 HTTP 主机匹配 |
| [test_round9_setup_markdown.py](test_round9_setup_markdown.py) | 模型覆盖配置、Setup API、Markdown 同步和自我档案读写 |
| [test_round10_tool_search_mcp.py](test_round10_tool_search_mcp.py) | 工具按需暴露与 MCP 命名 |
| [test_mcp_lifecycle.py](test_mcp_lifecycle.py) | 分页、ping、超时清理、配置校验、命名冲突与 HTTP 无前端入口 |
| [test_round11_drift.py](test_round11_drift.py) | 空闲判断、静默时段、开关、任务记录、同技能续跑和技能存在性 |

`round*` 文件名保留演进历史；当前能力按表中职责定位，无需按轮次顺序执行。


## 验证边界

- sqlite-vec 是可选后端；未安装时对应 KNN 用例跳过，要验证它请在项目环境安装 requirements-vector.txt。
- 工程测试不证明真实模型的提取/摘要质量，也不以 pytest 耗时代表线上检索延迟。
- 真实 MCP 接线与只读业务验证使用 scripts/check_mcp.py；飞书需先完成用户授权。
- 权限隔离测试覆盖共享治理及用户网关个人运行时；新增Claim与条件视图继续验证跨账号边界，不代表通用个人信息脱敏或操作系统级磁盘加密。
- tests/retrieval_metrics.py 保留通用排名指标计算；旧模拟轨迹、演示服务及对应评分 Runner 已删除。

## Claim与条件视图工程验证

[test_claims.py](test_claims.py)覆盖来源/公开归属拒绝、不可变版本、审批OCC、双时间/变化、条件/多值冲突、撤回/恢复、依赖失效、并发/回滚、TTL/缓存上限、抽取待审及来源await竞态；[test_claims_api.py](test_claims_api.py)覆盖真实网关两账号隔离、路由契约、有限状态问题与运行时/工具原子预算。全部临时输入只用于工程验证，不导入生产或报告为用户实验。2026-10-12最终全量359 passed / 84.08 s；[实现边界](../docs/implemented/Claim条件视图与访问路由.md)。

[test_source_retrieval.py](test_source_retrieval.py)覆盖未来消息缓存不得进入早期prefix、原文ID/词面/Dense融合、模型空间与内容绑定、坏分块及无效向量拒绝；研究工作流新增无reader纯召回和副本checkpoint/SHA检查。全部为临时工程输入；当前全量371 passed / 84.82 s，不是研究分数。
