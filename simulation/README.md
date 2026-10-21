# 模拟记忆功能验收

2026-10-20由用户明确授权创建。**这里是虚构人物与虚构原文，仅用于功能验收，不是真实用户资料、人工gold或LoCoMo成绩。**

[scenarios.json](scenarios.json)当前v4包含29组场景、39项问题检查；在原27组37项上新增六主题动态观察、六项文档事实两组，原断言保留。[expectations.json](expectations.json)是独立的AI编写断言。输入中没有答案标签。模型先执行正式Kernel/worker/Formation/审核/Claim/View/Reader，结束后才解释断言；没有测试专用提取器、手工补批准或修改成功答案。

## 覆盖内容

| 层次 | 场景与模块 |
| --- | --- |
| 真LLM处理模拟原文 | 12类槽位；冷/热View；否定饮食约束；他人/助手/问题/未来计划/敏感信息/引用注入；复杂条件；重复自述/替代来源；单值冲突/撤回恢复；跨会话Pattern；资料更新/撤回；外部作者；任务反馈；三资料巩固 |
| 真实本地BGE，无远程LLM | 3条直接录入的检索夹具，包含超512-token长文本；Keyword/BM25/Dense/RRF实际索引及目标父记忆召回 |
| 工程故障与操作测试 | tests中完整回归：双时间/修订链、条件/TTL/会话范围、支持组、来源撤回、版本并发、预算/引用/截断、DOCX/TXT解析、账号隔离、渠道入口及事务；这里使用临时库与受控替身，不冒充真模型调用 |

真实模型测试使用现有账号的模型配置，**账号仅用于选择配置，不读取或复制其历史**。模拟库与原始返回落本目录被Git忽略的runs，不能导入生产库。独立人物都绑定synthetic主体。pytest仍使用系统临时目录，不把普通单元测试缓存塞到本目录。

## 运行

实体三阶段的同预算对照另有[输入](entity_inputs.json)、[后验断言](entity_expectations.json)和[独立命令](../scripts/evaluate_entity_retrieval.py)：8场景×三路/过滤/实体RRF共24记录，实际本地BGE，最多可选6次生产Reader调用。直接夹具入库不评价抽取；字符预算不冒称Token预算。原始候选/选择/成本与HTML仅保存到runs，详见[协议与边界](../docs/implemented/实体身份与实体召回.md#第三阶段固定预算的实体对照)。

使用项目.venv、已有账号；先验证，默认0模型调用：

```powershell
.\.venv\Scripts\python.exe scripts/test_memory_scenarios.py --user YOUR_USER
.\.venv\Scripts\python.exe scripts/test_memory_scenarios.py --user YOUR_USER --execute --allow-remote --max-calls 112 --max-input-chars 600000
```

只重测指定场景；本机系统代理不通时可显式选择进程内直连，仍使用原LLM主机并验证TLS，不改系统代理或config：

```powershell
.\.venv\Scripts\python.exe scripts/test_memory_scenarios.py --user YOUR_USER --execute --allow-remote --direct-http --max-calls 12 --only slot_diet pending_conflict
.\.venv\Scripts\python.exe scripts/test_memory_scenarios.py --user YOUR_USER --offline-retrieval --execute
.\.venv\Scripts\python.exe scripts/test_memory_scenarios.py --user YOUR_USER --summarize
```

每次执行创建独立目录，保留input、库、逐阶段原始输出/usage、逐题trace、JSON报告和HTML报告；调用上限覆盖观察/审核/巩固/Reader。不同执行的调用预算分别计算，用户预算应同时计入旧失败；本批次累计119次，未超过开始声明的128次总上限。默认不自动续跑、不隐藏重试。

summarize只读取冻结产物并验SHA，选择与**当前固定输入完全相同**的最新尝试；执行失败也保留，不能筛选成功样本。输出跨版本累计清单及所有历史，不冒充一次最终版本全量重跑，也不计算研究准确率。后续变更断言/输入应登记新fixture_revision，旧快照不覆盖。

## 完整验收与历史诊断

最终同一核心指纹：完整工程1205 passed（687.74秒，0失败/错误/跳过）；真模型模拟37/37（92调用、86505已知tokens、usage未知0）；实际本地BGE四路通过。详见下方实现记录；这是功能验收，不是研究准确率。

最终验收按根[AGENTS](../AGENTS.md)执行：完整pytest tests、同一最终核心指纹的整套27组/37检查、真实本地BGE四路全部通过。核心/测试/输入变更后须重新验收；非零退出码、任何未执行/失败/预算阻断都不能称完整成功。--only只作定向诊断，summarize仍只作跨版本历史观察。

当前固定输入fixture_revision=3：在原有场景上增加默认中文、面试英文、面试简洁及撤回全部来源的反例，原断言不放宽。默认验证0调用；每轮显式设置全阶段调用/输入字符上限，失败原始输出与费用保留，不隐式重试。

首批v2的跨版本累计32/34与两项缺口属于历史记录，不作为最终验收。2026-10-20补齐有限条件自动发布、候选分句/条件Span绑定与未知前提保留，以及同值自述到Claim独立来源组的自动reinforce/approve和每组父记忆依赖。精确副本不重复加独立票，单值状态冲突/未知条件/敏感时间风险仍保留辅助。

详细机制、各轮失败/耗时、完整最终验收与部署范围见[实现记录](../docs/implemented/模拟场景功能验收与缺口.md#条件自动发布与独立来源强化2026-10-20)和[研究计划56](../docs/plans/长期陪伴记忆研究与实验计划书.md#56-条件准入与独立来源自动强化2026-10-20)。runs中的report.html可在本机浏览器打开；原始返回/库/账号信息不提交远程。模拟功能验收不代表真实长期效果、抽取F1或LoCoMo新成绩。
