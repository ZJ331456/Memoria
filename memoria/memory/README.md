# Memory：记忆版本、统一访问与证据选择

[核心包](../README.md) · [运行时](../runtime/README.md) · [分层说明](../../docs/implemented/记忆分层与配置.md) · [Studio/统一访问](../../docs/implemented/MemoryStudio与统一记忆访问.md)

本包实现当前登录账号的普通记忆、情景记忆及其访问链路。结构化 Claim 的写入/审批位于 [claims.py](../claims.py)，共享空间由 [governance.py](../governance.py) 管理；三者不自动互相复制。

## 文件映射

| 文件 | 职责 |
| --- | --- |
| [engine.py](engine.py) | 普通记忆写入、强化/替代/纠正、有效区间、词面/BM25/Dense/RRF 和图扩展 |
| [planner.py](planner.py) | 问候门控及可选 fast 模型查询/类型/数量规划 |
| [access.py](access.py) | QueryRequest/MemoryAccessOrchestrator：Claim、时间、来源复核、候选和选择解释 |
| [experience.py](experience.py) | 账号原话版本化BM25块索引、有界同消息邻块、recorded截止与回填诊断；不是批准状态 |
| [reader.py](reader.py) | memory-answer、服务端展示引用目录、严格回答schema、来源/版本新鲜度与缓冲输出 |
| [evidence_sufficiency.py](evidence_sufficiency.py)、[answer_risk.py](answer_risk.py)、[adaptive_fallback.py](adaptive_fallback.py) | 必要事实与原文引用核验、Query-only风险/回答支持、有限分级补检；生产前后检查显式开启，完整补检先做独立benchmark消融 |
| [temporal_query.py](temporal_query.py) | 有限中文年月日/相对日期解析，区间、身份和获知截止歧义处理 |
| [evidence_selector.py](evidence_selector.py)、[evidence/](evidence/README.md) | Top-K/Coverage/词面MMR/成本归一覆盖；完整JSON字符/字节/可信reader tokenizer预算与全候选trace |
| [claim_normalizer.py](claim_normalizer.py)、[condition_parser.py](condition_parser.py) | 原12槽位的有限别名、默认/活动条件与分句继承，独立条件证据 |
| [temporal_resolver.py](temporal_resolver.py)、[query_router.py](query_router.py) | 明确源事件精度、规则双时间区间与opt-in模型槽位建议 |
| [update_policy.py](update_policy.py) | Create/Reinforce/Transition/Review独立影子策略，不写库 |
| [claim_analysis.py](claim_analysis.py) | 更新建议及明确开启的模型支持度建议；不批准、不持久化 |
| [claim_relations.py](claim_relations.py) | typed scope/时间/多值关系与4类更新策略，完整目标复核 |
| [evidence_verifier.py](evidence_verifier.py) | 来源硬校验、词面风险、事件边界与可选支持度模型 |
| [embedding.py](embedding.py) | HTTP/本地 BGE、查询指令、文档分块、批次和向量校验 |
| [local_bge.py](local_bge.py)、[token_chunks.py](token_chunks.py) | 离线 CUDA/CPU、模型指纹、tokenizer 分块、连续原文 span 和完整覆盖 |
| [worker.py](worker.py) | 持久租约抽取、普通记忆审核队列及非空闲也执行的有界索引维护 |
| [episodic.py](episodic.py)、[forgetting.py](forgetting.py) | 来源任务情景、相关性召回、固定、TTL/容量归档 |
| [layer_settings.py](layer_settings.py)、[budget.py](budget.py) | 分层配置、读取次数和累计字符配额 |
| [layer.py](layer.py)、[markdown.py](markdown.py) | MEMORY.md 投影、SELF.md 和历史 PENDING.md 容器 |

## 写入和一致性

自动对话抽取由MemoryJobWorker写入memory_reviews，再由[auto_review.py](auto_review.py)执行LLM主审与服务器风险检查，低风险原子入库，疑问保留人工辅助；manual可关闭自动发布。完成状态和具体入库决定分别记录。手动 API 和明确授权的 memorize 直接写普通记忆，不能等同于 Claim 审批。

Claim显式抽取附带shadow_updates，额外模型调用0；analyze升级独立核验/关系/策略，支持度和关系模型分别opt-in。原analyze仍只建议；普通worker的低风险自动审核现可附简单Claim，其他演化仍需治理。[演化策略](../../docs/implemented/Claim证据核验与演化策略.md)。

普通记忆的创建、纠正、替代及来源撤销在 Store 事务内维护 `[valid_at, invalid_at)` 区间。恢复旧版本新建区间，保留中间变化；as_of 后词面、BM25、向量与图扩展均受时间过滤。正文相同会强化；其他候选可由同类型预筛后的 fast 决策器保守分类。详见 [一致性](../../docs/implemented/记忆一致性与检索审查.md)。

向量按模型命名空间与维度隔离。本地 BGE 长记忆保存连续 tokenizer 块、独立向量和原文 span，以父版本最佳块召回；更正、删除、撤销和时间查询同步限制块。批次完整校验后事务保存，延迟回填重验版本/正文/模型。HTTP 模型端点有输入限额，完整分块能力见 [长记忆索引](../../docs/implemented/实验记录与长记忆分块索引.md)。

## 统一读取

`/api/memory-query` 支持 unified/claim/keyword/bm25/dense/rrf。统一模式优先已批准 Claim；不完整时补检普通记忆及原始经历，只把仍有效、属于合法用户原话的来源列作未批准证据，不能消除 Claim 的缺项或冲突。公开导入、丢失和撤销来源被排除；模型 await 后重验来源/版本。

recorded_as_of 时跳过只有事实时间的普通记忆通道；原始经历支持记录截止，但不伪造事实发生时间。中文年份/月/日保留日历区间，以修订时间线展示，不编造某日期或把整个区间当已证明的状态。规则未覆盖、主体不明或获知区间需精确截止时返回 clarify。

明确平时/默认用scope_mode=default只读无条件状态，面试场景匹配activity；未给条件仍澄清。临时条件仅在原话明确“本次会话/this session”且同会话证据完整时绑定session_id；Chat传实际会话，其他会话忽略例外，未知绑定待审/澄清，不猜事件到期。当前编译v6，模式和会话进缓存键，精确时间不覆盖条件冲突。

Claim同槽位条件/否定保持完整组。四选择器同池/同预算，不用gold；费用单位明确，远程reader tokenizer不可核验时422而不冒用BGE。Dense专用诊断不伪装词面结果。API默认0模型调用；opt-in查询模型仅读query，API仍不调用reader。有限条件/事件精度与知识区间已经接入，通用条件与真实QA/长期收益仍未知，详见[本轮说明](../../docs/implemented/条件时序抽取与预算证据选择.md)。

2026-10-15新增[来源支持与渐进访问](../../docs/implemented/来源支持与渐进访问修复.md)：Claim组内AND/组间OR与保守旧格式；四选择器按同版本原文区间去重；unified缺项时补账号原始经历BM25（include_experiences=false可消融），最多16锚点、4锚点展开同消息邻块、总邻块不超过8。recorded截止在排名前过滤，fact_as_of不使用缺少事实时间的原始经历。公开/assistant来源不能作为本人经历，补检不自动填平Claim缺项/冲突。经历Dense/RRF现已可选实现；跨消息事件仍待实现。

普通父记忆以最佳块排名、最多3个近最佳块进入提示词/recall，父级RRF只占一票，旧正文SHA不复用。父正文不自动等同用户原话；普通精确完整候选已增加版本化来源组，相似强化不增加替代支持；语义独立性、同预算覆盖和真实质量收益未知。

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

## 有引用约束的Reader

新增/api/memory-answer复用QueryRequest与统一访问，在适用完整证据可呈现时调用main reader。严格JSON/引用展示集/审批/条件整组/来源span-SHA/新鲜状态校验，失败拒答，缺项可不调用；不把来源补检变成自动批准。旧memory-query保持诊断，新接口可能产生远程费用。有限运行时个人状态分支复用同契约，通用自由文本尚未全部覆盖，semantic_entailment_verified=false。[完整预算/配置/边界](../../docs/implemented/完整Reader预算与生产引用校验.md)。

本地BGE另支持明确英文small v1.5模型（384维/英文检索指令/独立指纹），用于LoCoMo英文独立实验，生产中文配置保持；同样离线safetensors、512 Token完整分块、无远端代码。[英文评测协议](../../docs/implemented/LoCoMo全量评测与英文检索修复.md)。

充分性/风险三模块已做完整LoCoMo消融，工程全量548项通过；风险组对抗拒答更高但普通F1下降，综合目标未达，不替换生产默认。`use_answer_risk`仅显式前后核验；完整生产补检仍待主体/条件/双时间及已批准来源约束接入。算法下一步为事实分解/状态校准与整段Reader+支持预算，[实际结果与限制](../../docs/implemented/证据充分性与风险感知回答.md#v2完整1986题消融2026-10-14)。

v3工程修复已接入[verification.py](verification.py)：缺项/冲突不一致最多一次完整重生成且负向不自动升充分；Reader前原子预留回答/支持/一次支持修复，核验length有界恢复并保留失败/usage。生产显式开关最多5次，默认关闭；受影响88项通过，0新模型调用，v2全量1题失败和原分数保持。[本次范围](../../docs/implemented/证据充分性与风险感知回答.md#v3工程修复状态预算与截断2026-10-14)。

存储与检索一致性的最新修复及验收见[逐项记录](../../docs/implemented/内部记忆一致性修复记录.md)。

`lexical.py`统一写入与召回词元，TF/长度由Store持久化；`review.py`在明确核对后原子联动叙事记忆与Claim批准。索引按全库缺项由后台维护，读取不回填文档；普通记忆、历史工具与已注入任务来源参与输出前依赖校验。元数据按请求批量加载，不使用跨请求旧快照缓存。

无关键词历史与在线Markdown导出同样复用统一来源/有效区间过滤；撤销优先于历史回放，审计记录保留。[修复证据](../../docs/implemented/算法逻辑审查逐项修复.md)。

强化目标在模型等待期间删除/替代时返回版本冲突，审核恢复待审；重新核对后重试。[修复证据](../../docs/implemented/算法逻辑审查逐项修复.md)。

Claim视图v3跟踪未选中条件Claim关联普通记忆的未来区间边界，空/默认状态缓存也随生效和到期重选；不复用v2缓存。[修复证据](../../docs/implemented/算法逻辑审查逐项修复.md)。

风险回答支持检查改用原回答字符区间完整覆盖，连接词、否定和数值符号不可因词项重复而漏检；仍不宣称语义蕴含已证明。[修复证据](../../docs/implemented/算法逻辑审查逐项修复.md)。

Query-only风险不再把月份/人名May当作推测授权；只有有限明确情态语法和其他显式推测表达可放宽，歧义保持保守。[修复证据](../../docs/implemented/算法逻辑审查逐项修复.md)。

[source_support.py](source_support.py)管理普通完整来源组、保守迁移、来源失效和存活引用；[生产重放与支持契约](../../docs/implemented/生产重放与普通来源支持.md)记录实际边界。

经历补检新增opt-in experience_mode=dense/rrf；独立版本化向量、来源/截止过滤、后台限批/退避与查询后复核已实现，默认bm25。Dense为合格向量精确扫描，RRF为经历BM25+Dense两路；[真实本机检查与边界](../../docs/implemented/生产重放与普通来源支持.md#3-可选经历denserrf与小规模真实检查)。

[ordinary_update_policy.py](ordinary_update_policy.py)约束普通非精确关系，有限绑定筛选先于Top-3；值/极性、并存/替代和事务版本复核防止明显不兼容更新。未知主体/条件不自动覆盖；精确正文去重、完整语义与12槽位扩展边界见[实施说明](../../docs/implemented/生产重放与普通来源支持.md#4-普通非精确关系的受约束更新)。

2026-10-16[LLM主审机制](../../docs/implemented/LLM自动审核与人工辅助.md)新增审计与有限历史重处理，保留原文归属/撤销/条件和事务检查；模型结果不作为现实核验。
