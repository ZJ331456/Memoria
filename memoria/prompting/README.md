# Prompting：低信任上下文与字符预算

[核心包](../README.md) · [运行时](../runtime/README.md) · [分层配置](../../docs/implemented/记忆分层与配置.md)

## 文件与流程

| 文件 | 职责 |
| --- | --- |
| [assembler.py](assembler.py) | PromptSection/PromptAssembler、分区统计和 atomic 结构化段 |
| [budget.py](budget.py) | ContextBudget、完整工具组、近期消息和帧裁剪 |
| [__init__.py](__init__.py) | 导出接口 |

身份指令是首条 system；记忆、Claim 证据、摘要、SELF、技能和目录进入有边界的低信任上下文，不把来源指令当系统命令。运行时追加近期真实消息。

## 两级预算

Assembler 先限制分区再限制整帧。默认语义 3000、情景 1800、程序 2400、摘要 2000、目录 1200、SELF 1200 字符，整体 12000；统计原始/使用长度和裁剪状态。

ContextBudget 每次主模型调用前计消息正文、tool calls 与每消息开销，保护首条系统、最新用户副本和帧，保留完整 call/result 组。过长用户仅裁剪模型副本，原文已存库；系统放不下报错，不能留下孤立工具结果。

Claim atomic 段与 `[claim-access-atomic]` 工具结果整段保留或明确省略，再次压缩仍保护条件/否定/引用。统一访问只注入 selection，不注入全部候选。初始帧也计入 memory/budget.py 的整轮读取配额。

这是 Unicode 字符近似，不是 reader tokenizer，不保证输入加 max_tokens 适配窗口。模板 char_budget=60000 应按实际模型调整；精确 token 是待实现事项。低信任证据遵守效果另测。

## 验证

在仓库根目录：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_layered_context_budget.py tests/test_memory_layers_integration.py tests/test_claims_api.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_round7_akashic.py::test_prompt_assembler_builds_system_context_frame tests/test_core.py::test_context_budget_keeps_recent_and_valid_tool_protocol
```
