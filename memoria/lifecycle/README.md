# Lifecycle：五阶段扩展点

[核心包](../README.md) · [运行时](../runtime/README.md) · [追踪](../observability/README.md)

[pipeline.py](pipeline.py) 提供 Phase/TurnContext/Pipeline，[__init__.py](__init__.py) 导出。上下文含 session/输入/模型消息/召回/工具/回复/metadata。register 的 priority 小者先执行，同优先级保留注册顺序；inspect 供运行概览。

| 阶段 | 实际位置 |
| --- | --- |
| before_turn | 用户消息保存前 |
| before_reasoning | 召回/帧组装后，首次模型调用前 |
| after_step | 每批工具结果写回后 |
| after_reasoning | 回复形成后，助手消息保存前 |
| after_turn | 回复保存/抽取投递后，完成 trace 前 |

默认没有预注册处理器或独立 TOML 开关，扩展方在装配时注册。异常传播至 runtime 产生 failed；本模块不提供自动事务回滚、重试、跨进程队列或权限沙箱，写入应走对应治理接口。

## 验证

```powershell
# 仓库根目录
.\.venv\Scripts\python.exe -m pytest -q tests/test_core.py::test_lifecycle_priority tests/test_memory_layers_integration.py
```
