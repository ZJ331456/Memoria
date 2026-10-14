# LLM：本地与远程统一调用

[核心包](../README.md) · [后端](backends/README.md) · [模型配置/真实验证](../../docs/implemented/本地与远程OpenAI统一接口.md)

业务用 LLMClient 的 OpenAI 形状 messages/tools/ChatResult。本地/远程 HTTP 走同一协议路径，local://cuda|cpu|auto 是进程内选项。Embedding 客户端在 [memory/embedding.py](../memory/embedding.py)，本地 HTTP 可以同时服务 BGE。

## 文件映射

| 文件 | 职责 |
| --- | --- |
| [client.py](client.py) | chat/chat_stream/complete、普通记忆抽取/关系决策/查询规划，完整请求门槛及ChatResult.budget |
| [types.py](types.py) | ChatResult 和供应商/上下文/内容安全异常 |
| [server.py](server.py) | 有限 OpenAI 兼容聊天、Embedding、models、health与不生成的reader-token-count扩展 |
| [backends/](backends/README.md) | HTTP/进程内后端、路由、本地队列 |
| [__init__.py](__init__.py) | 对外导出 |

## 本地模型服务

仓库根目录；先依模型文档在 `.venv` 安装本地依赖及准备权重：

```powershell
.\.venv\Scripts\python.exe scripts/serve_local_llm.py --model model/Qwen3.5-2B --embedding-model model/bge-small-zh-v1.5 --device auto --port 8080
# 只读检查登记，不等于生成成功
Invoke-RestMethod http://127.0.0.1:8080/health
Invoke-RestMethod http://127.0.0.1:8080/v1/models
```

此进程与 main.py 账号/记忆服务分开。main/fast base_url=`http://127.0.0.1:8080/v1`，model 与登记一致；BGE 用 bge-small-zh-v1.5。可选 --warmup 会真正加载/生成。权重仅留 model/，不自动下载；统一[requirements.txt](../../requirements.txt)已包含本地模型依赖，PyTorch的CPU/CUDA构建仍按硬件选择。

## 接口与部署边界

本地支持文本、function tools、SSE、usage/finish_reason、float/base64 512 维 BGE。仅协议子集，不支持图片/音频、任意供应商参数、strict schema 或全部 OpenAI API；无效模型/字段/输入明确报错。

本地生成按真实tokenizer校验输入加输出，有界串行队列/取消/超时；业务客户端新增完整请求预算，可信本地按相同模板计Token，远程按UTF-8请求字节门槛并明确Token未知。显式reader_local_count启用本地HTTP计数，绑定失败拒绝生成。可解析工具不证明 Qwen 抽取或长期记忆质量。本地模型 HTTP 当前无独立认证，默认只绑回环，不应直接公开为商用入口。

远程 Key/模型由管理员私密配置，公开只显配置状态。显式 Thinking 开关仅对官方 DeepSeek 端点发送，不泛用到任意兼容服务；真实可用性/费用/质量由实际配置和评测确定。

## 验证

```powershell
# 仓库根目录
.\.venv\Scripts\python.exe -m pytest -q tests/test_llm_backends.py tests/test_local_llm_slots.py tests/test_openai_compat.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_embedding_client.py tests/test_local_embedding.py tests/test_token_chunks.py
```

工程替身不发起付费调用，不证明实际权重效果；真实记录见模型文档/研究账本。

[完整预算与引用契约](../../docs/implemented/完整Reader预算与生产引用校验.md)说明配置与范围；新增[test_reader_contract.py](../../tests/test_reader_contract.py)核验HTTP实际body与计数一致、流式、绑定失败和输出预留。

HTTP普通/流式调用使用[tls.py](tls.py)缓存CA验证上下文，冷载在线程；证书路径/文件/目录变化刷新，hostname/CERT_REQUIRED保留，不共享用户HTTP会话/凭据。用于修复Windows重复载CA阻塞异步循环。[实测与边界](../../docs/implemented/LoCoMo全量评测与英文检索修复.md)。
