# 模拟记忆功能验收

2026-10-20由用户明确授权创建。**这里是虚构人物与虚构原文，仅用于功能验收，不是真实用户资料、人工gold或LoCoMo成绩。**

[scenarios.json](scenarios.json)包含27组场景、34项问题检查；[expectations.json](expectations.json)是独立的AI编写断言。输入中没有答案标签。模型先执行正式Kernel/worker/Formation/审核/Claim/View/Reader，结束后才解释断言；没有测试专用提取器、手工补批准或修改成功答案。

## 覆盖内容

| 层次 | 场景与模块 |
| --- | --- |
| 真LLM处理模拟原文 | 12类槽位；冷/热View；否定饮食约束；他人/助手/问题/未来计划/敏感信息/引用注入；复杂条件；重复自述/替代来源；单值冲突/撤回恢复；跨会话Pattern；资料更新/撤回；外部作者；任务反馈；三资料巩固 |
| 真实本地BGE，无远程LLM | 3条直接录入的检索夹具，包含超512-token长文本；Keyword/BM25/Dense/RRF实际索引及目标父记忆召回 |
| 工程故障与操作测试 | tests中完整回归：双时间/修订链、条件/TTL/会话范围、支持组、来源撤回、版本并发、预算/引用/截断、DOCX/TXT解析、账号隔离、渠道入口及事务；这里使用临时库与受控替身，不冒充真模型调用 |

真实模型测试使用现有账号的模型配置，**账号仅用于选择配置，不读取或复制其历史**。模拟库与原始返回落本目录被Git忽略的runs，不能导入生产库。独立人物都绑定synthetic主体。pytest仍使用系统临时目录，不把普通单元测试缓存塞到本目录。

## 运行

使用项目.venv、已有账号；先验证，默认0模型调用：

```powershell
.\.venv\Scripts\python.exe scripts/test_memory_scenarios.py --user YOUR_USER
.\.venv\Scripts\python.exe scripts/test_memory_scenarios.py --user YOUR_USER --execute --allow-remote --max-calls 96 --max-input-chars 500000
```

只重测指定场景；本机系统代理不通时可显式选择进程内直连，仍使用原LLM主机并验证TLS，不改系统代理或config：

```powershell
.\.venv\Scripts\python.exe scripts/test_memory_scenarios.py --user YOUR_USER --execute --allow-remote --direct-http --max-calls 12 --only slot_diet pending_conflict
.\.venv\Scripts\python.exe scripts/test_memory_scenarios.py --user YOUR_USER --offline-retrieval --execute
.\.venv\Scripts\python.exe scripts/test_memory_scenarios.py --user YOUR_USER --summarize
```

每次执行创建独立目录，保留input、库、逐阶段原始输出/usage、逐题trace、JSON报告和HTML报告；调用上限覆盖观察/审核/巩固/Reader。不同执行的调用预算分别计算，用户预算应同时计入旧失败；本批次累计119次，未超过开始声明的128次总上限。默认不自动续跑、不隐藏重试。

summarize只读取冻结产物并验SHA，选择与**当前固定输入完全相同**的最新尝试；执行失败也保留，不能筛选成功样本。输出跨版本累计清单及所有历史，不冒充一次最终版本全量重跑，也不计算研究准确率。后续变更断言/输入应登记新fixture_revision，旧快照不覆盖。

## 本批结果

首轮27组/34检查：27满足断言、5能力缺口、1安全失败、1执行失败。后续针对失败定位与复测，并保留5次代理连接失败和1次模型把反馈错当主引文的失败。

累计清单：**32项满足当前断言，2项仍是能力缺口**，不是32/34的研究准确率，也不是全部场景在最终代码上重新调用过模型。共119次实际调用、85,566已知tokens，另5次usage未知；0生产模拟数据写入。修复了Supported+review未形成冲突提案、否定约束读出及Reader新鲜度参数不一致；改进跨会话主题标签复用、资料项目主体/反馈引文约束与来源Reader可引用层级提示。真BGE四种模式均召回本次长文本目标，不据此宣称通用召回率或性能收益。

仍未通过：

1. **复杂条件全自动发布**：默认/面试例外被保留为3条待审候选，未自动发布为3条条件Claim；安全待审不等于自动化功能完整。
2. **重复自述的Claim独立来源强化**：新自述被批准，但只复用旧Claim，未把新来源接入其独立支持组；撤回首条后，仍有普通记忆却无法回答该Claim状态。底层手动支持组功能通过工程测试，生产自动接线尚缺，不能报为强化已完整。

完整结果、各轮耗时与后续验收见[实现记录](../docs/implemented/模拟场景功能验收与缺口.md)及[研究计划55](../docs/plans/长期陪伴记忆研究与实验计划书.md#55-模拟数据的核心功能验收2026-10-20)。runs中的report.html可在本机浏览器打开；原始返回/库/账号信息不提交远程。
