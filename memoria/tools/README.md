# Tools：注册、发现、授权与限额

[核心包](../README.md) · [运行时](../runtime/README.md) · [MCP](../mcp/README.md) · [技能](../skills/README.md)

## 文件映射

| 文件 | 职责 |
| --- | --- |
| [registry.py](registry.py) | Tool 元数据、严格参数校验、hooks、授权、超时与输出限额 |
| [builtin.py](builtin.py) | 个人记忆/Claim、历史、情景、时间、计算、技能与 HTTP GET |
| [search.py](search.py) | 按需 schema、tool_search/tool_call 解码 |
| [policy.py](policy.py) | 识别明确记住/遗忘意图，授予本轮相应个人写工具 |
| [loop_guard.py](loop_guard.py) | 稳定批次签名与第三次重复阻断 |
| [http_get.py](http_get.py) | 初始 URL 主机白名单、文本获取和截断 |

## 能力与配额

build_registry 注册 recall_memory/memorize/forget_memory/search_history/current_time/calculate/load_skill/http_get；有情景服务时增加 recall_episodes，有 Claim 服务时增加只读 access_claims。Claim 工具使用严格主体/槽位/条件/双时间请求，不包含创建或批准。

search_enabled 开启时常驻工具及搜索/调用包装器直接展示，其余 schema 按需发现；关闭则暴露普通工具。MCP 接同一注册表并受相同校验/权限/超时/输出限制。

每轮 recall_memory/recall_episodes/search_history/load_skill/access_claims 共享默认 6 次/12000 字符，初始帧计入。包装调用按真实解码名称记账，Claim 超额整体省略。API 调试没有聊天轮次配额，但有单次校验/限额，写调试需 confirm_write=true。

## 写入与网络边界

memorize 直接写普通记忆，forget_memory 删除普通记忆；技能指令、执行成功、Claim 批准和共享提案是不同动作。共享使用独立 Agent key/ACL/审批。

写权限仅识别未被引用的明确肯定命令；否定、回忆及询问不授予能力。同轮同项能力的明确禁止优先，歧义需要明确指令。[逐项修复与工程证据](../../docs/implemented/算法逻辑审查逐项修复.md)。

calculate 仅 AST 基本算术，load_skill 返回正文不执行脚本。HTTP 白名单只检查初始主机且跟随重定向，当前不是完整 SSRF/目标隔离或商用网络沙箱。结果/原话也不是可信指令。

## 验证

在仓库根目录：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_round10_tool_search_mcp.py tests/test_skills.py tests/test_claims_api.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_optimizations.py::test_tool_permissions_hooks_validation_and_output_cap tests/test_core.py::test_tool_loop_guard_uses_stable_argument_signature
```

真实外部服务另由 [check_mcp.py](../../scripts/check_mcp.py) 检查，不由替身测试证明。
