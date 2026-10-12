# 已实现：Claim证据核验与影子演化策略

核对：2026-10-12。拆分证据核验、关系建议和更新策略；数据库事务仍由ClaimMemory负责。这里只生成建议，不自动批准、关闭旧事实或把模型输出标成独立验证。已有真实模型成绩不变，新策略真实语义质量尚未评测。

## 入口与职责

| 模块 | 实际职责 |
| --- | --- |
| [EvidenceVerifier](../../memoria/memory/evidence_verifier.py) | 复用角色/账号/公共归属/撤回/SHA/span/父版本校验；值、主体、条件、计划、否定与事件边界词面风险 |
| [ClaimRelationResolver](../../memoria/memory/claim_relations.py) | 同主体/槽位目标快照、typed scope和时间关系、可选模型关系、四类更新建议 |
| [claim_analysis](../../memoria/memory/claim_analysis.py) | 候选及目标完整快照、可选模型调用、await后复核、只读响应 |
| [ClaimMemory.extract](../../memoria/claims.py) | 原单条/最多3候选抽取；返回pending及shadow_updates，额外模型调用0 |

`POST /api/claims/{id}/analyze`请求示例（revision替换为实际版本）：

```json
{"expected_revision": 1, "use_model": false, "use_relation_model": false, "peer_limit": 32}
```

use_model是原支持度模型建议；use_relation_model为独立关系建议，默认均关闭。调用可能使用当前账号配置的远程服务，usage/relation_usage分别返回；不能当作免费的离线规则或独立judge。model_revision未知。

响应含evidence_verification、update.action/reason/relations/targets/truncated、mode=shadow、persisted=false、automatic_approvals=0、semantic_support_verified=false及实际model_calls。无相关目标时关系模型跳过，不将配置的模型名冒充已执行调用。目标含id/revision/revision_id；它们不是可跳过版本检查的提交凭证。

## 核验规则及限度

先复用来源硬校验，再保守检查：值是否字面存在、self是否混入第三方、无scope是否丢条件、statement是否夹带计划、否定是否与极性矛盾。仅能词面绑定activity=interview；其他typed scope保留scope_binding_requires_review，不宣称通用条件理解已完成。

英文值允许紧邻中文，但不在英文长词中做子串匹配。未出现字面值要求语义/别名审核，不改写原值。复杂引用、混合句、计划完成可能误报；passing不是语义蕴含或现实核验。模型Supported不能消除这些风险。

抽取器不生成事件时间。已给出的边界也须原话依据：day要求UTC+8零点及同一值所在语句的明确日期；exact须原话包含相同的带时区ISO时刻。普通日期不支持任意时分秒，另一语句的日期/变化词不绑定当前值。这是有限词面规则，不是通用时间/因果解析。未知时间不从observed_at/recorded_at补造。

## 关系与更新策略

| 关系 | 策略与约束 |
| --- | --- |
| duplicate | 值/极性/模态/事实起止/精度/关联记忆/typed scope全同，reinforce；不凭相似度合并 |
| coexisting_value | 多值槽位新值create并保留旧值，新爱好不等于放弃旧爱好 |
| conditional_exception / disjoint_scope | 条件包含或不相交分域create；true与1不混同 |
| historical_coexist | 无重叠区间可create，未知边界不硬判无重叠 |
| state_change | 仅单值、同scope、双方positive statement、值不同、同值语句明确变化、新源边界位于旧区间内时transition建议 |
| conflict / scope_overlap / plan_resolution / unknown | review；冲突/计划完成/相似句不直接变supersede |

风险不能混过：强化/变化目标须唯一，任何不确定关系保留review；pending目标、不完整池、词面风险或模型不支持阻止可执行关系建议。transition始终只是建议，用户还需原revision/approve流程；不可变修订、双时间事务和generation/视图失效继续使用原实现。

规则目标默认32（可指定1–64），稳定ID扫描最多256根；池不完整标truncated并review。关系模型只接受完整的至多8个目标、12000字符上下文，超限拒绝而不截断。输出必须覆盖全部真实目标，无重复/虚构ID；evidence_quote唯一匹配候选原话并还原SHA/span。

模型可增加冲突/未知/变化等风险判断，不能用duplicate/coexisting_value绕过确定性门控。任何await后重查候选与整个相关目标/来源快照，修订、撤回、源改写或新增相关目标则409作废；无关槽位不使建议失效。无效结构/调用失败502且不写Claim。

## 默认与场景读取

AccessRequest新增scope_mode=applicable/default；applicable保留严格缺条件澄清，default只读无条件Claim且不能同时给context。模式进缓存键，编译器为deterministic-claims-v2，旧策略视图不复用。

QueryRequest新增scope_mode=auto/applicable/default；auto解析明确“平时/默认/通常情况下/一般情况下”，以及“面试模拟/模拟面试/面试时/面试的时候/面试中”activity=interview。默认与场景混用或显式参数矛盾时clarify，精确时间不能覆盖冲突。时间线同样遵守default过滤。

明确默认只读无条件状态；明确场景按适用条件/具体程度；未给场景仍严格澄清。default不猜用户当前是否旅行，也不删除例外。一般条件、别名及通用语义路由待验收。

## 接入、验证与下一步

现有analyze/显式extract响应升级，Studio原分析按钮使用新版规则；关系模型可通过API显式开启。后台MemoryJobWorker仍只生产普通memory_reviews，未启用结构化Claim自动演化/额外付费任务。没有公开语料晋升本人Claim、旧历史读取、补造人物/时间或修改冻结gold。

工程反例见[测试](../../tests/test_claim_evolution.py)：有源变化但旧状态不改、缺日期/假时刻/跨句日期、偏好并存、条件例外、计划/主体/否定、模型假目标与引用、相关目标/来源变化、池截断/pending目标、0额外抽取调用、默认/场景及缓存隔离。相关117 passed / 36.76s；补充跨句与并发反例后全量 **432 passed / 159.55s**（新增27项）。墙钟为工程执行时间，临时输入不是用户历史或研究分数，本轮新真实模型调用0。

最后补齐实际model_calls元数据后，相关61 passed / 14.28s。12份变更Markdown的212个链接/锚点、15项CLI/图表检查、pip check通过；没有重跑真实收费模型或改既有研究成绩。

下一项研究：新增获准连续本人资料，测Claim Precision/Recall、Update Decision Macro-F1、False Supersede、Stale-Fact Answer与条件/主体误用；记录误报/未知、同候选/同预算、live/settled及人工审核依赖。独立真人研究评估继续暂缓。

LongMemEval官方说明历史经过属性控制构建，若使用须另作公开可比基准，不能变成真实用户历史，本轮未下载。[官方来源](https://github.com/xiaowu0162/LongMemEval)。设计参考[Mem0论文](https://arxiv.org/abs/2504.19413)与状态演化的[Inside Out论文](https://arxiv.org/abs/2601.05171)，没有复用其训练/成绩或宣称相同收益。当前为可测试的保守基线，尚未训练记忆决策模型。

## 本轮进一步深化

2026-10-12继续拆分normalizer/condition_parser/temporal_resolver/update_policy，增独立条件证据、同值事件精度和自然语言知识区间；旧文中“抽取不补事件时间/限定面试”等为上一轮边界。最新契约与真实同池选择负结果见[实现文档](条件时序抽取与预算证据选择.md)，来源一致性不等于语义批准，模型不能自动覆盖。
