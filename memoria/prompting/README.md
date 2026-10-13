# Prompting：低信任上下文与完整Reader预算

[核心包](../README.md) · [运行时](../runtime/README.md) · [分层配置](../../docs/implemented/记忆分层与配置.md)

## 文件与流程

| 文件 | 职责 |
| --- | --- |
| [assembler.py](assembler.py) | PromptSection/PromptAssembler、分区统计和 atomic 结构化段 |
| [budget.py](budget.py) | ContextBudget、完整工具组、近期消息和帧裁剪 |
| [reader_budget.py](reader_budget.py) | 完整请求字节、可信本地聊天Token、输出预留、整协议组裁剪与硬门槛 |
| [__init__.py](__init__.py) | 导出接口 |

身份指令是首条 system；记忆、Claim 证据、摘要、SELF、技能和目录进入有边界的低信任上下文，不把来源指令当系统命令。运行时追加近期真实消息。

## 分区、证据与完整请求预算

Assembler 先限制分区再限制整帧。默认语义 3000、情景 1800、程序 2400、摘要 2000、目录 1200、SELF 1200 字符，整体 12000；统计原始/使用长度和裁剪状态。

ContextBudget 每次主模型调用前计消息正文、tool calls 与每消息开销，保护首条系统、最新用户副本和帧，保留完整 call/result 组。过长用户仅裁剪模型副本，原文已存库；系统放不下报错，不能留下孤立工具结果。

Claim atomic 段与 `[claim-access-atomic]` 工具结果整段保留或明确省略，再次压缩仍保护条件/否定/引用。统一访问只注入 selection，不注入全部候选。初始帧也计入 memory/budget.py 的整轮读取配额。

以上是Unicode字符近似。新增ReaderBudget覆盖实际system/query/history/tool calls/results/schemas/生成参数，先删完整可选组再压缩帧，保护完整当前query和工具定义。默认完整HTTP请求上限240000字节，输出max_tokens单独预留；local://或显式启用的可信Memoria本地HTTP按真实聊天模板核验窗口，远程Token/窗口保持未知。严格记忆reader对证据整组省略拒答，不发布脱离条件的值。详见[完整预算与引用契约](../../docs/implemented/完整Reader预算与生产引用校验.md)。

## 验证

在仓库根目录：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_layered_context_budget.py tests/test_memory_layers_integration.py tests/test_claims_api.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_round7_akashic.py::test_prompt_assembler_builds_system_context_frame tests/test_core.py::test_context_budget_keeps_recent_and_valid_tool_protocol
```

.venv工程验收新增[test_reader_contract.py](../../tests/test_reader_contract.py)；完整495项通过不证明语义质量或长期效果。
