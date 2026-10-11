# 本地与远程 OpenAI 兼容接口

更新日期：2026-10-11。当前 Windows 本机使用 Qwen3.5-2B 与 BGE small zh v1.5；依赖安装在项目独立 `.venv`，模型在 Git 忽略的 `model/`。本机推理服务提供业务所需的 Chat Completions / Embeddings 子集，不实现 OpenAI 的全部产品接口。

## 推荐运行方式

在仓库根目录开两个 PowerShell 终端：

```powershell
# 终端一：同一服务提供聊天与 BGE；只监听本机。
.\.venv\Scripts\python.exe scripts/serve_local_llm.py --model model/Qwen3.5-2B --embedding-model model/bge-small-zh-v1.5 --device cuda --port 8080 --warmup

# 终端二：Memoria 业务服务。
.\.venv\Scripts\python.exe main.py
```

已有进程需要重启以加载修复。默认总上下文为 **8192 token（输入加输出预算）**，不是可再生成 8192 token；本机已将 `[agent].max_tokens` 调为 1024，给输入留出空间。可用 `--max-context-tokens` 调整服务预算，实际硬件是否容纳需要另外测量。超过预算返回 `context_length_exceeded`，不静默截断历史。

BGE 单次编码输入上限为 **512 token，含 special token**。直接向编码接口发送超长输入返回 HTTP 400、`context_length_exceeded`、`param=input`；进程后端同样拒绝静默截断。生产长记忆的写入、更正和回填已自动分块，保存每块向量与原文位置，检索按最佳块召回父版本；旧单向量数据会识别为待回填。见[分块与实验说明](实验记录与长记忆分块索引.md)。LongMemEval / LoCoMo 的来源前缀也计入分块预算，见[评测说明](../eval/README.md)。

推荐模型槽位配置：

```toml
[llm.main]
model = "model/Qwen3.5-2B"
api_key = ""
base_url = "http://127.0.0.1:8080/v1"

[llm.fast]
model = "model/Qwen3.5-2B"
api_key = ""
base_url = "http://127.0.0.1:8080/v1"

[memory.embedding]
model = "bge-small-zh-v1.5"
api_key = ""
base_url = "http://127.0.0.1:8080/v1"

[agent]
max_tokens = 1024
```

`fast` 用于记忆提取、关系判断、检索规划；仅改 main 不能保证这些工作已经本地化。本机配置已将三槽位统一到上述服务，原私有配置备份于 `data/backups/config_before_openai_20261011.toml` 和 `models_before_openai_20261011.toml`，这些文件不提交。

切换远程时设置对应槽位的远程模型名、`base_url` 与 Key，业务调用保持相同。Keyless loopback 不经过环境代理；远程服务仍保留环境代理与重试。页面保存时空 Key 继续表示保持原值；若编辑私有 override 文件显式写 `api_key = ""`，则清除继承的远程 Key。

## 调用边界

| 接口 / 能力 | 当前行为 |
| --- | --- |
| `GET /health` | 服务存活、模型是否加载、设备和总上下文预算；存活不代表模型已成功推理 |
| `GET /v1/models` | 列出已配置的聊天与 Embedding 模型；未配置 Embedding 时只列聊天 |
| `POST /v1/chat/completions` | 文本消息、普通响应与 SSE；每次生成唯一 completion ID |
| 输出预算 | `max_tokens` 或 `max_completion_tokens` 二选一；1–8192，默认 256，并受总上下文限制 |
| 采样 | `temperature` 0–2、`top_p` (0,1]；默认 temperature=0 为确定性生成，>0 才开启采样 |
| 文本内容 | 字符串或 `type=text` 内容段；system/developer/user/assistant/tool 角色 |
| 工具 | function tools、`tool_choice=auto/none`；参数在 HTTP 中为 JSON 字符串，模板内部转换为对象 |
| 流式文本 | 生成过程中输出文本 delta；思考/工具标签跨片段过滤，避免进入可见正文 |
| 流式工具 | 有 tools 的回合先完成生成、解析后返回带 `index` 的结构化调用；不会逐 token 发布尚未完成的工具参数 |
| 完成与 usage | 正确区分 stop/length/tool_calls；`include_usage=true` 时最后单独返回 `choices=[]` usage chunk，然后 `[DONE]` |
| `POST /v1/embeddings` | 单个字符串或 1–64 个非空字符串；512 维、FP32、CLS、L2；float 与 little-endian float32 base64 |
| Embedding usage | 实际编码的 token 数，包括特殊 token，按 512-token 截断后的输入统计 |
| 错误 | 参数错误 400、未知模型 404、队列满 429、超时 504；OpenAI 形状 `error.message/type/param/code` |

当前为纯文本 CausalLM 推理；虽然 Qwen checkpoint 含视觉组件，这个后端不处理图片/视频/音频。`n>1`、stop sequences、seed、response_format、logprobs、strict schema decoding、强制 required/指定工具等不支持的参数返回 400，而不是假装生效。SDK/API 的完整兼容范围应以此表为准。

流式响应开始后无法再改 HTTP 状态码，推理错误通过 SSE `error` 返回；客户端会将其转换为 `ProviderError`。正文或工具片段已经输出后的中断不自动重试，以免重复回答或重复动作。没有结束标记的截断流不会当成成功。内部 reasoning 不作为正文 fallback。

本地或远程回复的 finish_reason 为 length 时保留该原因，但不给业务层返回可执行工具调用，避免把截断参数当成完整动作；原始调用片段仍可用于检查。

## Embedding 与索引

Memoria 对本机 HTTP 中模型名 `bge-small-zh-v1.5` 或对应目录名的查询添加中文检索前缀；写入、纠正、回填不加。HTTP 接口自身只编码输入，不猜测输入是查询还是文档，其他 SDK 调用查询时应自行添加前缀。使用自定义 Embedding 名称时也应显式处理该策略。

本地服务冻结进程内已加载的模型身份，BGE 文件指纹只在该部署首次获取编码器时计算，避免每个查询重复读取约 96 MB。修改权重需要重启服务。正式实验需固定 revision/策略；换权重应使用新模型标识或端点并建立新索引。

HTTP BGE 与原 `local://` BGE 使用不同索引命名空间，切换后旧向量保留，新空间按回填流程生成；第一次查询可能包含回填成本。生产长文已分块且不静默截断；检索阈值校准、后台索引和全库迁移仍待后续实验。HTTP 客户端分块需要项目中匹配的 BGE tokenizer，编码策略或权重变更需要重新部署、隔离索引。

## 进程内备用路径

可以把 main/fast 的 base_url 设为 `local://cuda`，模型为 `model/Qwen3.5-2B`；Embedding 则用 `model/bge-small-zh-v1.5`。无需独立 HTTP 服务，仍经 `LLMClient` 的同一消息/结果接口，普通文本支持实时 delta。有工具时同样先生成再解析。一般优先独立 HTTP，避免两个应用进程重复加载 LLM 权重。

本地 LLM 串行推理、最多 4 个运行/待运行任务；超时和取消会设置 stopping criteria，取消排队任务或在生成步之间终止活动生成。模型加载/一次 GPU kernel 无法瞬时中断，槽位在真实任务结束时释放。模型仅从本地 Safetensors 加载，不启用远程 Python 代码。

## 已完成验证

- 真实 RTX 4060 Laptop GPU 同进程服务加载 Qwen3.5-2B 与 BGE，以 OpenAI SDK **3.28.0** 通过模型列表、普通聊天、实时文本 SSE、默认 base64 Embedding、计算工具及工具结果续答；未调用外部 API、未使用个人记忆库。
- 合成工具请求生成 `calculate({"expression":"1+2"})`，结果回传后回答 3。一次文本流产生 30 个非空文本片段，保留 stop 与 usage；这只是接口检查，不是模型能力或延迟 SLO。
- 最终定向 10 个测试文件 **124 passed、2 skipped**，两个 skip 为未安装的可选 sqlite-vec。前端 TypeScript/Vite production build 与 `pip check` 通过。

本机原始记录：[SDK/GPU smoke](../data/reports/local_openai_compat_smoke.json)。日志和报告被 Git 忽略；上述汇总可随代码入库。复现真实模型检查需在独立环境安装可选 SDK：

```powershell
.\.venv\Scripts\python.exe -m pip install openai==3.28.0
.\.venv\Scripts\python.exe scripts/check_local_openai.py
```

该脚本启动临时本机服务、用合成输入检查、写报告后结束自己的子进程，不启动或修改个人 Memoria 数据库。既有业务代码仍使用 httpx，不依赖 OpenAI SDK。后续长期记忆质量实验见[实验计划书](长期陪伴记忆研究与实验计划书.md)。
