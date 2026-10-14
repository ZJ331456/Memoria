# Runtime：个人 Agent 轮次编排

[核心包](../README.md) · [记忆](../memory/README.md) · [上下文](../prompting/README.md) · [生命周期](../lifecycle/README.md)

## 文件与入口

| 文件 | 职责 |
| --- | --- |
| [agent.py](agent.py) | AgentRuntime.run、访问/工具循环、流式事件、取消、配额与 trace |
| [compaction.py](compaction.py) | SessionCompactor 增量摘要、分片续读、来源覆盖与 CAS 保存 |
| [../service.py](../service.py) | 装配依赖，向账号内 API 提供轮次服务 |

HTTP 聊天 API 保留；Memory Studio 是来源/状态管理界面，不提供聊天窗。运行时由登录网关选择当前账号，不能通过客户端参数指定他人目录。

## 一轮路径

1. 创建上下文与 tracer，执行 before_turn 并保存用户消息。
2. 少量明确个人状态问题走 MemoryAccessOrchestrator，跳过旧摘要和 fast 查询规划；一般任务执行增量摘要、普通记忆规划/召回及情景召回。
3. 组装低信任上下文帧、技能和工具目录，追加近期消息，执行 before_reasoning。
4. 主模型调用前应用字符预算和完整Reader预算（含输出预留）；工具校验/风险授权/配额通过后可并行执行，完整工具组回送模型，触发 after_step。
5. 回复形成后执行 after_reasoning，保存助手消息及抽取任务；执行 after_turn，保存完成/失败/取消 trace 与来源短情景。

有限个人问题的 reader 只取得 selection、缺项和限制，未选中调试候选不另行注入。缺条件/冲突/未经批准来源仍须未知；原子省略不能留下脱离条件的值。access_claims 仍接受明确槽位，不授予写权限。

## 压缩、预算与取消

增量压缩从未覆盖前缀推进，默认每次最多 2 批、每批 80 条、输入 24000 字符、摘要 6000 字符。超长消息以 partial_message_id/partial_offset 续读，完整读入才 covered；来源失效重建、无新增复用、CAS 防并发覆盖。摘要有损，覆盖列表成本随历史增长，不是长期大库常数时间方案。

max_iterations=0 仍受 200 步硬上限，正值最多 200。上下文超限以 45% 紧急字符预算重试；第三次重复工具批次阻断。相关读取共享默认 6 次/12000 字符，初始帧计入；字符预算不保证适配每个tokenizer；完整ReaderBudget为可信本地核验真实聊天窗口，为远程执行字节门槛，重试/循环保护后最终调用也重新计量。

取消向上传播并保存 cancelled，情景异常不覆盖主响应。turn_kind=drift 复用运行时但不生成自身情景/普通抽取。共享空间没有贯穿此轮次的 agent_id/space_id，独立治理权限不替代个人工具策略。

## 验证

在仓库根目录：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_core.py tests/test_incremental_compaction.py tests/test_memory_layers_integration.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_claims_api.py tests/test_memory_studio.py tests/test_optimizations.py::test_sse_chat_endpoint_emits_delta_and_complete
```

真实 reader 是否遵守引用和未知边界另做 [研究评测](../experiments/README.md)，不由工具循环测试推断。

## 严格个人记忆回答

已有有限个人状态规则分支使用CitationGuard，只发送实际选中完整Claim/条件和服务器引用目录。生成先缓冲，检查严格字段、展示ID、审批、账号/span-SHA及调用后的新鲜状态；after_reasoning改写拒绝，最终复核与助手入库同事务，之后才发delta。缺证据/条件或预算不足可直接澄清。通用聊天、工具规划和access_claims后的自由文本尚未全部转为契约；校验不是语义蕴含证明。[实现范围](../../docs/implemented/完整Reader预算与生产引用校验.md) · [工程案例](../../tests/test_reader_contract.py)。

Claim与情景工具现在携带独立于输出文本的来源/状态依赖，Reader前及发布前复核并缓冲输出；Claim直接重新选择状态。工程反例与剩余边界见[逐项修复](../../docs/implemented/算法逻辑审查逐项修复.md)。
