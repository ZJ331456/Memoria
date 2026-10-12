# Drift Digest：限额空闲小结

[技能目录](../README.md) · [指令](SKILL.md) · [Drift 调度](../../memoria/drift/README.md)

recall_memory 小范围抽查，必要时 search_history 核对，区分可靠/疑似冲突/可忽略，列用户待确认事项。仅本轮写授权允许时可 memorize，禁止 forget_memory。

虽列模板技能名单，Drift 默认关闭，正式网关也关闭账号自动 Drift。配置/跳过原因见调度器，名称不自动启动任务/授权。内部首次调试可把写工具名单设为空。

SKILL 描述提到 PENDING/SELF，当前无专用文件读取工具，不声称检查全文；完成摘要可能写 PENDING 历史容器，但不是批准 Claim。没有外部通知或共享自动提案，也不替代审核。

## 验证

```powershell
# 仓库根目录
.\.venv\Scripts\python.exe -m pytest -q tests/test_round11_drift.py
```

开关/跳过/记录/同技能续跑测试，不以 completed 当整理质量或长期成绩。
