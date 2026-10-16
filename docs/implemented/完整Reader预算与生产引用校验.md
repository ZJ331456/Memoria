# 完整 Reader 预算与生产引用校验

更新：2026-10-13。实现位于 [ReaderBudget](../../memoria/prompting/reader_budget.py)、[CitationGuard](../../memoria/memory/reader.py)、[运行时](../../memoria/runtime/agent.py)和[模型客户端](../../memoria/llm/client.py)。本次完成工程保护；没有新增 QA 成绩、Claim Extraction F1 或真实长期陪伴结论。[后续验收](../plans/长期陪伴记忆研究与实验计划书.md#21-完整reader预算生产引用校验与数据范围核对)。

## 1. 预算覆盖完整请求

证据选择预算只衡量选中证据，旧 ContextBudget 仍负责字符裁剪。新增 ReaderBudget 在实际调用前处理完整消息、system、历史、tool calls/results、工具定义和生成参数，冻结模型与输出上限。HTTP 计量与发送复用同一个 payload 构造器，普通/流式、紧急压缩重试及循环保护后的最终调用均有预算检查。

| 调用方式 | 计量与限制 |
| --- | --- |
| 任意 HTTP / 远程，默认 | 完整请求 JSON 的 UTF-8 字节数，上限默认 240000；单独记录并请求输出 max_tokens。远程输入 token 与供应商窗口是否适配保持未知 |
| local:// 进程 | 真实本地聊天 tokenizer、与生成相同的模板和特殊 token；输入 token + 输出预留 + 128 安全 token ≤ 模型/部署/配置的较小窗口，同时满足字节上限 |
| Memoria 本地 HTTP，显式启用 | 调用同端点的 /v1/reader-token-count，核对协议、模型、整数计数及 tokenizer SHA，再执行相同的完整窗口约束 |

输出 token 与输入字节不会相加成伪 Token；BGE 不用于计量 reader。未经可信本地计数器核验而设置 reader_context_tokens 会拒绝调用。计数接口不可用、模型不匹配、缺指纹或重定向时也拒绝，不静默退回估算。

ReaderBudget.prepare 按完整协议组移除较早的可选消息，再压缩上下文帧；保护系统提示、完整当前 query 和工具 schema，不在这个阶段截断它们。结构化 Claim 的条件、否定与引用整组保留或整组省略，省略后严格 reader 不输出事实。如果必须保留的输入或输出预留仍超限，调用前返回明确的预算失败。

通用任务此前的字符裁剪策略仍存在；完整请求门槛不宣称所有通用历史都完整保留。结构化 reader 额外检查当前 query 与完整证据包实际呈现，旧裁剪使它们不完整时拒绝回答。

## 2. 配置与本地计数接口

管理员在项目私有 config/system.toml 的已有 agent.context 表中合并以下字段，修改后重启业务服务；不要创建重复 TOML 表：

```toml
[agent.context]
reader_input_bytes = 240000
reader_safety_tokens = 128
reader_local_count = false
# reader_context_tokens = 8192
```

使用升级后的 Memoria 本地 HTTP 服务时可将 reader_local_count 设为 true；它仅对受配置信任的回环 HTTP 模型生效，不用于远程 DeepSeek。此时可以再设置 reader_context_tokens 限制窗口。local:// 自动读取可信本地 tokenizer，不需要此开关。默认部署窗口 8192，仍受模型元数据约束；计数结果不证明显存足够。

POST /v1/reader-token-count 接受与本地 chat/completions 相同的请求字段，返回 protocol、model、input_tokens、context_tokens、tokenizer_sha256，不加载生成权重、不生成文本。模型别名与参数验证复用生成路径。这是 Memoria 的扩展接口，不是通用 OpenAI 标准。它信任管理员部署的本地服务，并非远程密码学证明；当前模型服务仍只绑定回环，没有独立公网认证。更换 tokenizer、模板或模型文件后须重启业务与模型服务，避免进程缓存保留旧资产。

ChatResult.budget 与运行 trace 保存 input_bytes、可核验时的 input_tokens、reserved_output_tokens、context_tokens、计数来源、窗口核验状态与裁剪统计。未知字段保持 null/unknown，不用供应商事后 usage 反推调用前已保证窗口。

## 3. 生产记忆回答与引用契约

新登录接口 POST /api/memory-answer 与只读诊断 /api/memory-query 使用同一个 QueryRequest。前者会在适用证据完整且预算允许时调用当前账号的 main reader；可能调用远程付费模型。后者保持检索/选择诊断，不调用 reader；显式查询模型选项的行为保持不变。

在 /docs 登录后使用请求示例：

```json
{"query":"我平时希望用什么语言回答？","mode":"unified","selector":"coverage","budget_chars":4096}
```

账号由登录网关选择，不接受客户端 user_id。响应含 status、answer、citations、validation、access；真正进入 reader 时还含 reader_budget、usage、reader_called。缺少适用 Claim、条件冲突、来源失效或证据包超限时可直接 clarify，无需让模型猜测。已有公开语料不会成为 qin 的本人 Claim。

生产严格通道要求模型生成 JSON：status=answer/clarify/abstain、answer、evidence_ids。服务端依据**实际展示的选中证据**构建引用目录：Claim 使用根 ID 与不可变 revision，原话来源使用 memory ID 与完整内容 SHA。ID、审批状态和来源来自服务器，不接受用户粘贴的“引用目录”。

| 校验 | 失败行为 |
| --- | --- |
| 严格字段、重复 JSON key、重复 ID、工具调用、未展示或待审引用 | 拒绝原始模型结论，返回固定拒答 |
| 回答缺引用、当前状态缺少完整已展示 Claim 引用、正文额外引用未知 ID | 拒绝，不将来源数量当语义充分性 |
| 当前完整 query 或原子证据包没有实际呈现 | 不调用 reader 或拒绝输出 |
| 原话角色/账号、span 与 SHA、条件证据、公开导入排除 | 来源不可用即拒绝，历史查询也不恢复撤回的来源 |
| 模型 await 前后状态/时间线、版本、有效区间与来源复核 | 调用期间纠正、撤销、删除或改写后拒绝旧结论 |
| clarify/abstain 内容夹带事实 | 使用固定未知提示，不发布未经核验的原文 |

少量已有规则覆盖的个人状态问题在 AgentRuntime 中走相同严格通道，先缓冲生成，验证后才发送 SSE delta；不会把未校验的内容先流给用户。after_reasoning hook 不能改写已经通过的事实回答。最终来源复核与助手消息入库共用 Store 事务；add_message 使用可嵌套保存点，避免提前提交调用者事务。

范围必须区分：完整预算覆盖 LLMClient 的普通与流式调用；严格引用约束覆盖新 memory-answer 和运行时有限个人状态通道。通用聊天/工具规划的最终自由文本尚未全部改为此契约，access_claims 工具后的通用总结也仍待统一。引用通过表示展示、审批、归属、来源和新鲜度符合契约，**不证明自然语言结论与证据语义相符**；semantic_entailment_verified=false 保留。将来撤回来源也不能撤销用户已经读过的历史答案。

## 4. 本轮验证

- 项目 .venv 相关测试 106 passed / 55.51s；最终全量 **495 passed / 198.58s**。新增 test_reader_contract.py 的 32 项参数化工程案例，涵盖实际 HTTP body、流式、输出预留、整组裁剪、绑定失败、引用伪造、审批、版本竞态、hook、事务回滚和登录隔离。
- 实际 Qwen3.5-2B tokenizer 使用完整临时 system/user/工具调用/工具结果/工具定义：292 输入 token，完整请求 490 UTF-8 字节，窗口 8192，输出预留 256，安全量 128。直接模板计数与本地 HTTP 路由一致；tokenizer SHA 为 2020d3d2e69de53b11ff6dbd4e0f917223d344a198bd88c6f3d38b661152cca1。
- tokenizer 核验不加载模型权重、生成调用 0；临时工程输入不导入 qin。本轮没有付费 QA、Embedding 重算、新批准个人 Claim 或旧历史导入。测试墙钟不是生产延迟，也不作为研究质量。
- 文档验收：20 份变更 Markdown 的 345 个本地链接/锚点、16 项 CLI/历史图表检查与 pip check 通过；示例配置仅增加已核对的 Reader 默认项，已有配置值不变。私有配置与凭据未修改，原话/逐题结果/本地 HTML/面试材料不提交。

## 5. 数据评测到底覆盖什么

本机注册研究清单与冻结输入指纹核对见[公开审计](../../assets/evaluation/dataset-scope-20261013.json)。实际是 **1 个真实来源数据集**，不是 3 个：

| 层级 | 数量与含义 |
| --- | --- |
| 已下载 LCCC-base 验证归档 | 20000 条非空记录，未对整个归档做全量评估 |
| 当前研究子集 | 50 段公开对话、206 条消息；人物、事件时间、跨会话连续身份未知 |
| QA 协议 | 同一子集的 v1、v2 两个版本，各 64 题；v1 保留，v2 为现行版本，不是两个新数据集 |
| 每轮当前 QA/召回诊断 | 完整 64 题，41 可知 / 23 不可知；不是从 20000 条中全部建题和评估 |
| Window / Dense / RRF | 三种对照方法；研究 RRF 的两路来源检索与生产三路加权 RRF 也需区分 |

每题只看到合法历史前缀，受窗口/证据预算约束；“全 64 题”不代表全历史都进入模型。多轮重复不增加独立样本数。现有 gold 是 AI 辅助开发标注，独立真人评估继续暂缓；Claim F1、条件/演化质量与真实长期效果仍未知。本轮没有增加第二、第三个来源数据集或重跑原始归档。

下一步先做保留来源和条件的紧凑 reader 投影，建立新预算/引用协议，比较同候选同预算下 QA、拒答、越界失败与真实 usage；保留历史 13 次越界及全部旧结果。连续个人资料、独立标签、正向视图收益和商业备份/恢复按计划继续等待或推进。
