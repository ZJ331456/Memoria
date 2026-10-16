# 本地与远程 OpenAI 兼容接口

更新日期：2026-10-13。Windows 本机保留 Qwen3.5-2B 与 BGE small zh v1.5；qin 的 main/fast 已按真实公开来源开发诊断切到 deepseek-flash，embedding 保留本地 BGE；依赖安装在项目独立 `.venv`，模型在 Git 忽略的 `model/`。本机推理服务提供业务所需的 Chat Completions / Embeddings 子集，不实现 OpenAI 的全部产品接口。

2026-10-16配置入口已归拢：根config.toml引用configs/local/models.toml，模型显式backend/device，账号覆盖与加密profile仍独立。[目录、优先级及API/本地配置](配置目录与模型后端.md)。

## 当前账号配置与 DeepSeek

2026-10-12 本机 qin 已激活账号独立的 DeepSeek main/fast；真实登录读取模型配置通过。Key 保存在 DPAPI 私密 profile，override 仅使用 `credential_profile = "deepseek"`，绑定模型/端点/账号，配 `thinking = "disabled"`。显式思考开关只发给官方 api.deepseek.com HTTPS；其他供应商和 local:// 不接收。其他账号不继承 qin Key；本机管理员默认本地槽位仍可供本地对照。配置与真实调用结果见 [研究工具](真实历史标注与评测.md)。

## 本地对照运行方式

在仓库根目录开两个 PowerShell 终端：

```powershell
# 终端一：同一服务提供聊天与 BGE；只监听本机。
.\.venv\Scripts\python.exe scripts/serve_local_llm.py --model model/Qwen3.5-2B --embedding-model model/bge-small-zh-v1.5 --device cuda --port 8080 --warmup

# 终端二：Memoria 业务服务。
.\.venv\Scripts\python.exe main.py
```

已有进程需要重启以加载修复。默认总上下文为 **8192 token（输入加输出预算）**，不是可再生成 8192 token；本机已将 `[agent].max_tokens` 调为 1024，给输入留出空间。可用 `--max-context-tokens` 调整服务预算，实际硬件是否容纳需要另外测量。超过预算返回 `context_length_exceeded`，不静默截断历史。

BGE 单次编码输入上限为 **512 token，含 special token**。直接向编码接口发送超长输入返回 HTTP 400、`context_length_exceeded`、`param=input`；进程后端同样拒绝静默截断。生产长记忆的写入、更正和回填已自动分块，保存每块向量与原文位置，检索按最佳块召回父版本；旧单向量数据会识别为待回填。见[分块与实验说明](实验记录与长记忆分块索引.md)。来源前缀存在时同样计入 token 预算；旧模拟评测入口已移除。

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

`fast` 用于记忆提取、关系判断、检索规划；仅改 main 不能保证这些工作已经本地化。本机配置已将三槽位统一到上述服务，原私有配置备份封存于 `data/legacy/pre-users/backups/config_before_openai_20261011.toml` 和同目录 `models_before_openai_20261011.toml`，这些文件不提交。

模型配置由本机管理员维护：默认槽位在 `configs/local/models.toml`（由根models.file指定），单用户覆盖在 `data/users/<UUID>/config/models.override.toml`，修改后重启业务服务。正式登录网关的 `PUT /api/settings/models` 返回 403，防止用户将管理员 Key 或模型进程路径改到其他位置；当前没有配置页面。

切换远程时，在上述私有文件设置对应槽位的远程模型名、`base_url` 与 Key，业务调用保持相同。Keyless loopback 不经过环境代理；远程服务仍保留环境代理与重试。私有 override 文件显式写 `api_key = ""` 会清除继承的远程 Key，不在文档或 Git 写真实值。用户隔离、qin 登录和权限范围见 [登录说明](用户登录与数据隔离.md)。

## 调用边界

| 接口 / 能力 | 当前行为 |
| --- | --- |
| `GET /health` | 服务存活、模型是否加载、设备和总上下文预算；存活不代表模型已成功推理 |
| `GET /v1/models` | 列出已配置的聊天与 Embedding 模型；未配置 Embedding 时只列聊天 |
| `POST /v1/chat/completions` | 文本消息、普通响应与 SSE；每次生成唯一 completion ID |
| `POST /v1/reader-token-count` | Memoria扩展：同聊天字段验证，完整模板输入Token/窗口/tokenizer指纹；不加载生成权重或生成 |
| 输出预算 | `max_tokens` 或 `max_completion_tokens` 二选一；1–8192，默认 256，并受总上下文限制 |
| 采样 | `temperature` 0–2、`top_p` (0,1]；默认 temperature=0 为确定性生成，>0 才开启采样 |
| 文本内容 | 字符串或 `type=text` 内容段；system/developer/user/assistant/tool 角色 |
| 工具 | function tools、`tool_choice=auto/none`；参数在 HTTP 中为 JSON 字符串，模板内部转换为对象 |
| 流式文本 | 生成过程中输出文本 delta；思考/工具标签跨片段过滤，避免进入可见正文 |
| 流式工具 | 有 tools 的回合先完成生成、解析后返回带 `index` 的结构化调用；不会逐 token 发布尚未完成的工具参数 |
| 完成与 usage | 正确区分 stop/length/tool_calls；`include_usage=true` 时最后单独返回 `choices=[]` usage chunk，然后 `[DONE]` |
| `POST /v1/embeddings` | 单个字符串或 1–64 个非空字符串；512 维、FP32、CLS、L2；float 与 little-endian float32 base64 |
| Embedding usage | 实际编码的 token 数，包括特殊 token；单块最多 512 token，直接超长输入拒绝，生产文档先完整分块 |
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

## 工程验证与当前边界

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_local_llm_slots.py tests/test_llm_backends.py tests/test_openai_compat.py tests/test_local_embedding.py tests/test_embedding_client.py -q
```

旧合成 smoke 脚本和报告已移除。生产接口继续使用 httpx，不要求 OpenAI SDK。真实长期记忆实验见 [计划](../plans/长期陪伴记忆研究与实验计划书.md)；接口兼容不等于模型抽取/回答质量已经达标。

## 完整请求预算的后续接入

LLMClient普通/流式调用先检查完整payload字节上限，记录输出预留；local://按真实模板核验输入+输出+安全量。本地HTTP可在agent.context显式启用reader_local_count；只访问配置信任的回环计数接口，模型/指纹/协议不可核验时拒绝。远程DeepSeek仍使用请求字节门槛，精确输入Token与窗口未知；不能用BGE代替。调用构造器和模板与计量共用，实际Qwen tokenizer已核对292输入Token、0生成调用，详见[完整预算与引用说明](完整Reader预算与生产引用校验.md)。资产或配置变更需重启服务。

## HTTP证书上下文复用

2026-10-13修复本机HTTPX每次建client重复加载CA耗时数秒并阻塞asyncio的问题；普通/流式用同证书策略上下文缓存，冷载移到线程。SSL_CERT_FILE优先于SSL_CERT_DIR，再到certifi；证书路径/metadata/目录变化刷新，hostname与CERT_REQUIRED保留。仅缓存CA，不跨账号共享HTTP会话/凭据。实际耗时/检查点记录见[评测说明](LoCoMo全量评测与英文检索修复.md)。
