# Memory：记忆版本、统一访问与证据选择

[核心包](../README.md) · [运行时](../runtime/README.md) · [分层说明](../../docs/implemented/记忆分层与配置.md) · [Studio/统一访问](../../docs/implemented/MemoryStudio与统一记忆访问.md)

本包实现当前登录账号的普通记忆、情景记忆及其访问链路。结构化 Claim 的写入/审批位于 [claims.py](../claims.py)，共享空间由 [governance.py](../governance.py) 管理；三者不自动互相复制。

## 文件映射

| 文件 | 职责 |
| --- | --- |
| [engine.py](engine.py) | 普通记忆写入、强化/替代/纠正、有效区间、词面/BM25/Dense/RRF 和图扩展 |
| [planner.py](planner.py) | 问候门控及可选 fast 模型查询/类型/数量规划 |
| [access.py](access.py) | QueryRequest/MemoryAccessOrchestrator：Claim、时间、来源复核、候选和选择解释 |
| [temporal_query.py](temporal_query.py) | 有限中文年月日/相对日期解析，区间、身份和获知截止歧义处理 |
| [evidence_selector.py](evidence_selector.py) | 同一池的 top-k/coverage 整组选择；完整 JSON 的字符成本 |
| [claim_analysis.py](claim_analysis.py) | 更新建议及明确开启的模型支持度建议；不批准、不持久化 |
| [embedding.py](embedding.py) | HTTP/本地 BGE、查询指令、文档分块、批次和向量校验 |
| [local_bge.py](local_bge.py)、[token_chunks.py](token_chunks.py) | 离线 CUDA/CPU、模型指纹、tokenizer 分块、连续原文 span 和完整覆盖 |
| [worker.py](worker.py) | 持久租约抽取任务及普通记忆审核队列 |
| [episodic.py](episodic.py)、[forgetting.py](forgetting.py) | 来源任务情景、相关性召回、固定、TTL/容量归档 |
| [layer_settings.py](layer_settings.py)、[budget.py](budget.py) | 分层配置、读取次数和累计字符配额 |
| [layer.py](layer.py)、[markdown.py](markdown.py) | MEMORY.md 投影、SELF.md 和历史 PENDING.md 容器 |

## 写入和一致性

自动对话抽取由 MemoryJobWorker 写入 memory_reviews，先待审，批准才调用 remember；任务完成只代表抽取完成。手动 API 和明确授权的 memorize 直接写普通记忆，不能等同于 Claim 审批。

普通记忆的创建、纠正、替代及来源撤销在 Store 事务内维护 `[valid_at, invalid_at)` 区间。恢复旧版本新建区间，保留中间变化；as_of 后词面、BM25、向量与图扩展均受时间过滤。正文相同会强化；其他候选可由同类型预筛后的 fast 决策器保守分类。详见 [一致性](../../docs/implemented/记忆一致性与检索审查.md)。

向量按模型命名空间与维度隔离。本地 BGE 长记忆保存连续 tokenizer 块、独立向量和原文 span，以父版本最佳块召回；更正、删除、撤销和时间查询同步限制块。批次完整校验后事务保存，延迟回填重验版本/正文/模型。HTTP 模型端点有输入限额，完整分块能力见 [长记忆索引](../../docs/implemented/实验记录与长记忆分块索引.md)。

## 统一读取

`/api/memory-query` 支持 unified/claim/keyword/bm25/dense/rrf。统一模式优先已批准 Claim；不完整时补检普通记忆，只把仍有效、属于合法用户原话的来源列作未批准证据，不能消除 Claim 的缺项或冲突。公开导入、丢失和撤销来源被排除；模型 await 后重验来源/版本。

recorded_as_of 时跳过只有事实时间的普通记忆通道。中文年份/月/日保留日历区间，以修订时间线展示，不编造某日期或把整个区间当已证明的状态。规则未覆盖、主体不明或获知区间需精确截止时返回 clarify。

Claim 同槽位条件/否定保持完整组。top-k/coverage 用同池/预算比较，不用 gold；字符预算含 JSON，不是 reader token。Dense 专用诊断不会伪装成词面结果，可用性依赖编码配置。API 诊断不调用 reader，通用条件、精确 token、MMR 与选择质量实验仍待完成。

## 分层与来源生命周期

工作记忆是近期消息/摘要/帧，语义记忆是普通事实/偏好/目标有效版本，情景保存任务结果和来源，程序记忆加载 SKILL.md；遗忘是跨层治理。

情景默认 TTL 90 天、活动上限 1000、召回 top 3；固定记录可超过上限。管理可读归档，运行时只召回活动且未过期/固定记录。删除会话清理原话、情景、压缩、任务与候选，不代表删除全部已批准普通事实；完整遗忘需删除/撤销。Claim 原话失效隐藏值并使依赖视图失效。

MEMORY.md 从有效普通记忆重建，不是另一权威数据库；SELF.md 可注入，PENDING.md 不替代 SQLite 审核。默认分层总帧 12000 字符，每轮相关只读工具最多 6 次/12000 字符，初始帧计入。关闭语义见分层说明。

## 验证

在仓库根目录：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_memory_consistency.py tests/test_review_source_transactions.py tests/test_document_memory.py tests/test_token_chunks.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_claims.py tests/test_memory_studio.py tests/test_episodic_memory.py tests/test_memory_layers_integration.py
```

临时用例不代表真实模型质量。BGE 见 [Embedding](../../docs/implemented/本地Embedding配置.md)，真实研究走 [experiments/](../experiments/README.md)。
