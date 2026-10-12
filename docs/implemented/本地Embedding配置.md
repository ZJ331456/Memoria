# 本地中文 Embedding：BGE small zh v1.5

2026-10-12 更新：支持进程内 `local://cpu/cuda/auto` 和本机 OpenAI 兼容 HTTP 两种方式。默认示例为 Qwen/BGE 共用 `http://127.0.0.1:8080/v1`；远程 LLM 也可搭配进程内 BGE。完整启动顺序见[根 README](../../README.md#3-模型方案选择一种)与[统一接口](本地与远程OpenAI统一接口.md)。本页的文件规模与环境数字是 2026-10-10 的下载/环境记录，不是最新部署性能测量。

模型存储在项目根目录 `model/bge-small-zh-v1.5/`，依赖安装到项目 `.venv`。接入代码已在 main；模型文件、环境、私有 override 与原始报告不随 clone 提供。main/fast 的实际配置取决于默认配置及账号覆盖，本页不固定它们的供应商。

## 文件与环境

```text
Memoria-Agent/
├─ model/bge-small-zh-v1.5/      # Safetensors、tokenizer、配置与下载校验清单
├─ .venv/                      # 独立 Python 环境，不继承 base site-packages
├─ data/users/<UUID>/config/models.override.toml  # 当前账号槽位覆盖，忽略
├─ scripts/download_embedding.py
└─ scripts/serve_local_llm.py    # 可选独立 HTTP 推理服务
```

下载源为 [ModelScope BAAI/bge-small-zh-v1.5](https://www.modelscope.cn/models/BAAI/bge-small-zh-v1.5)。默认固定提交 `8399f11f8da998fe932df2684586c92024219d05`，选择 12 个模型/配置文件，合计 96,406,042 字节（约 91.94 MiB）。权重文件 `model.safetensors` 是 95,827,648 字节；重复的 `.bin` 权重不下载。

“12 文件 / 96,406,042 字节”只统计所选远端模型与配置文件；ModelScope 本地 SDK 的 `.msc` / `.mv` 元数据及额外生成的 `download_manifest.json` 不计入该口径。

ModelScope 1.34 的 snapshot 下载器只接受分支/标签。脚本先核验固定提交与公开分支的选中文件清单相同，再下载，在下载后重新核验远端清单和每个文件的 SHA256/大小；`download_manifest.json` 明确区分指定提交和下载分支。已有文件可复用缓存。

本机使用 Python 3.13、PyTorch 2.9.0 CUDA 12.6 与 Transformers 5.5.3。以下命令均在项目目录执行，用 `.venv` 的解释器，无需激活环境，也不向 conda base 安装依赖：

```powershell
# 新机器首次创建；已有 .venv 无需重复创建。
python -m venv .venv

# CUDA PyTorch 先使用官方索引安装；本机 RTX 4060 使用 CUDA 12.6 wheel。
.\.venv\Scripts\python.exe -m pip install "torch==2.9.0" --index-url https://download.pytorch.org/whl/cu126
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-local-embedding.txt pytest
.\.venv\Scripts\python.exe scripts/download_embedding.py
```

CUDA wheel 包含所需运行库，正常使用不需要另外安装完整 CUDA Toolkit。其他机器应先确认显卡驱动与所选 wheel 的兼容性。[PyTorch 官方安装说明](https://pytorch.org/get-started/previous-versions/)

检查独立环境：

```powershell
.\.venv\Scripts\python.exe -c "import sys, torch; print(sys.executable); print(sys.prefix != sys.base_prefix); print(torch.__version__); print(torch.cuda.is_available())"
Get-Content .venv/pyvenv.cfg
```

`include-system-site-packages` 应为 `false`。项目不会修改 conda base 的依赖。

当时核验环境为 Python 3.13.5、PyTorch 2.9.0+cu126、Transformers 5.5.3、SQLite 3.50.2，独立 `.venv` 不继承 base site-packages；`pip check` 通过。向量后端为 `auto`，当时未安装 `sqlite-vec`，不能声称验证了该扩展。私有报告不链接到公开仓库；SQLite 运行时升级的商业验收见[计划第18节](../plans/长期陪伴记忆研究与实验计划书.md#18-下一阶段功能优先级与商用准备)。

## 配置与启动

进程内方案在私密 `config.toml` 中设置；账号已有 `data/users/<UUID>/config/models.override.toml` 时，该文件覆盖默认槽位：

```toml
[memory.embedding]
model = "model/bge-small-zh-v1.5"
base_url = "local://cuda"
```

本地后端无需 API Key；旧 Embedding key 即使仍存于私有配置中也不会用于本地编码。`local://cuda` 明确要求 GPU，不可用时报告错误；`local://cpu` 明确走 CPU；`local://auto` 自动选择可用设备。模型相对路径按项目根目录解析。

使用独立环境启动后端：

```powershell
.\.venv\Scripts\python.exe main.py
```

已有业务进程需要重启才会应用启动配置。当前 Memory Studio 面向记忆管理，旧聊天前端和模型设置页已移除；模型配置由私密配置及管理员工具管理。切换配置不能自动安装 CUDA 依赖。

账号运行时初始化时预热进程内 BGE。预热独立使用 `max(120 秒, 常规 timeout_seconds)`，不修改之后的请求超时；HTTP/远程 Embedding 不由这一步调用。预热失败记录警告、保留配置修复入口，应检查设备、模型路径和依赖；服务存活不等于模型已就绪。main/fast 按各自配置判断可用性。

## 编码约定与索引隔离

当前 `local://` 后端针对该中文 small 模型实现。按 [BGE 官方模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5) 的检索约定，使用 512 维 CLS 向量、FP32 推理与 L2 归一化，每次输入最多 512 个 tokenizer token。

加载时如出现唯一的 `embeddings.position_ids` 为 `UNEXPECTED` 的提示，这是该 checkpoint 与当前 Transformers 非持久 buffer 的兼容提示。本机已核验该 tensor 为 `(1, 512)`，内容与 `arange(512)` 一致；当前 `BertEmbeddings` 将它注册为 `persistent=False`，因此不是模型权重缺失。此说明只针对已核验的这一个 key；其他加载异常仍需单独检查。

- 查询通过 `embed_query` 添加 `为这个句子生成表示以用于检索相关文章：`；记忆写入、纠正和索引回填不添加查询指令。
- 模型和 tokenizer 只从本地读取，`local_files_only=True`、`trust_remote_code=False`；推理不访问模型平台或远程 Embedding API。
- 进程中复用已加载模型，单个模型/设备使用一个串行工作线程。编码移出 asyncio 事件循环；最多 8 个运行/待运行任务，超出会报错。超时会取消尚未开始的任务，已经开始的 CUDA 任务运行结束后释放占位。
- 向量命名空间包含实际权重、tokenizer/配置内容与编码策略指纹，不包含 API Key 或设备名。创建客户端后、首次加载前如文件发生变化，加载会拒绝，避免新权重被写到旧命名空间。

模型切换后，原远程 Embedding 向量保留在自己的命名空间，不参与 BGE 相似度计算；新 BGE 向量按现有回填/重新索引流程生成。正式实验要使用隔离数据库并冻结模型文件；更新模型建议下载到新目录后切换配置。当前项目仍在读取时做有限回填，此次接入没有完成实验计划中的后台索引改造。

**相似度阈值与质量仍需校准。** 生产文档编码已按 tokenizer 完整分块，保留 span 并检索命中块；单块限 512 token，直接超长编码不会静默截断。长事件/Markdown 的实现见[分块说明](实验记录与长记忆分块索引.md)。接入成功不证明长期记忆质量提高；该模型主要用于中文，其他语言研究需另固定模型。

## 当前验证入口与边界

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_local_embedding.py tests/test_embedding_client.py tests/test_document_memory.py tests/test_token_chunks.py -q
```

模型 revision 为 `8399f11f8da998fe932df2684586c92024219d05`。本机使用项目虚拟环境，配置支持 CPU/CUDA 与本机 HTTP。旧四条合成记忆 smoke、报告和前端构建记录已移除，不作为真实用户实验成绩。

生产分块已实现，见 [长记忆分块](实验记录与长记忆分块索引.md)。真实对话已准备，见 [真实数据](真实对话数据与实验入口.md)；访问速度与长期陪伴质量尚需按 [研究计划](../plans/长期陪伴记忆研究与实验计划书.md) 验证。
