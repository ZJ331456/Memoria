# Drift：默认关闭的空闲整理

[核心包](../README.md) · [运行时](../runtime/README.md) · [技能](../skills/README.md) · [追踪](../observability/README.md)

[worker.py](worker.py) 的 DriftWorker 负责轮询/跳过/技能轮转/手动执行；[service.py](../service.py) 装配生命周期，[Store](../store.py) 保存 drift_runs 与接续摘要。

启用后检查活跃用户 turn、静默、空闲、间隔、日预算和上一轮，再于独立整理会话复用 runtime。同技能成功摘要可续用，记录步骤/摘要/trace。

skip_memory_enqueue=True 阻止自身普通抽取，Drift 也不生成自身情景。白名单收紧写工具，forget_memory 硬阻断。Markdown 开启时摘要可进入 PENDING.md 历史容器，不是批准 Claim 或 SQLite 审核。

## 配置和边界

`[agent.drift]` 默认关闭，配置空闲/间隔/步数/日预算/静默/技能/写工具/时区。GET /api/drift 与 POST /api/drift/run 均需登录；force 越过时间/日预算，不与活跃 turn 或未完任务重叠。

正式登录网关将账号 drift_enabled 设为 false，根模板开关不代表为注册用户开启自动任务。接口以实际跳过原因/状态为准。模型调用可能收费；未实现外部通知、持久分布式队列或共享空间自动提案，completed 只表示执行状态。

## 验证

```powershell
# 仓库根目录
.\.venv\Scripts\python.exe -m pytest -q tests/test_round11_drift.py
```

工程开关/跳过/预算/接续测试不证明真实整理质量。
