# Observability：耗时、trace 与指标

[memory_trace.py](memory_trace.py)还提供来源链、实际候选/选择/Claim/View快照、Reader请求片段可见性及后验归因；事务水位包含资料形成/版本。失败/未知usage保留，见[生产闭环](../../docs/implemented/生产记忆闭环与逐题归因.md)。

[核心包](../README.md) · [运行时](../runtime/README.md) · [实验账本](../experiments/README.md) · [API](../api/README.md)

## 文件映射

| 文件 | 职责 |
| --- | --- |
| [context.py](context.py) | ContextVar request/session/turn ID |
| [events.py](events.py) | 进程 EventBus 订阅/派发和监听异常处理 |
| [tracing.py](tracing.py) | 状态/步骤/召回 ID/工具/metadata 与常见凭据脱敏 |
| [timing.py](timing.py) | 实际阶段墙钟、调用/失败和公开统计 |
| [metrics.py](metrics.py) | HTTP 进程计数/耗时和 Store 的 Prometheus 文本 |

## 读取与解释

GET /api/traces 读当前账号持久 trace。召回快照保存 ID/类型/重要度/来源；工具保存脱敏参数/状态/耗时/预览，完成/失败/取消分开。来源/候选/选择 trace 不表示 reader 正确。

stage_timings 只记录执行过的压缩、规划、编码/检索、融合、prompt、reader 和工具。嵌套墙钟可能重叠，不直接相加。TTFT 仅非空可见流式文本，排除 reasoning/工具增量；非流式或未输出为 null。

正式 /metrics 需登录，metrics_enabled=false 返回 404。HTTP 计数重启清零，turn/runtime/jobs 从 Store 汇总；没有完整分位直方图、跨进程汇聚/告警或商用 SLO。EventBus 不是持久队列。

## 隐私与验证

递归遮蔽常见密钥字段、Bearer 和部分 sk 模式，不是完整个人信息脱敏。参数/错误/预览仍可能有用户内容，需要生产访问与保留策略。实验原始 prompt/响应留账号私有目录，Studio 只读账本摘要和已核验数值。

```powershell
# 仓库根目录
.\.venv\Scripts\python.exe -m pytest -q tests/test_core.py::test_trace_redacts_credentials tests/test_optimizations.py::test_trace_metadata_and_eval_metrics tests/test_round7_akashic.py::test_event_bus_and_request_context
.\.venv\Scripts\python.exe -m pytest -q tests/test_experiment_records.py tests/test_memory_studio.py
```

pytest 墙钟不是线上延迟，实际模型阶段见 [实验记录](../../docs/implemented/实验记录与长记忆分块索引.md)。

[memory_trace.py](memory_trace.py)提供账号事务内生命周期水位、访问捕获指纹、固定候选预算重放及后验阶段归因；不读取gold或给出语义评分。[范围与私密边界](../../docs/implemented/生产重放与普通来源支持.md)。
