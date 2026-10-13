# LLM Backends：路由与执行

[LLM 包](../README.md) · [统一接口](../../../docs/implemented/本地与远程OpenAI统一接口.md)

## 文件映射

| 文件 | 职责 |
| --- | --- |
| [base.py](base.py) | ChatBackend 协议/ChatResult 形状 |
| [resolve.py](resolve.py) | local:// 选进程内，其余 HTTP；标签仅诊断 |
| [openai_http.py](openai_http.py) | Chat Completions、SSE 工具拼接、usage、异常和有限重试 |
| [process.py](process.py) | 进程内配置、回调与统一异常 |
| [process_engine.py](process_engine.py) | 离线加载、模板、Qwen 工具/可见文本、token 窗口与队列 |
| [__init__.py](__init__.py) | 导出后端/路由 |

## 契约与失败行为

本地/远程 HTTP 复用路径，回环不受系统代理环境影响；本地可无 Key，远程须配置完整。供应商标签不表示每项厂商特性已验收。

网络/429/部分5xx 有界重试，上下文超限/内容拒绝分类传播。流式已有文本、reasoning 或工具增量后中断不重发；无效 JSON/工具参数或缺完成标记不假报完成，length 不执行不完整工具。

进程内路径以仓库根解析，离线加载且 trust_remote_code=False，设备使用 BF16/FP16/FP32。每模型/设备串行且队列有界；真实 tokenizer 校验输入/输出窗口。Thinking/tool 标签从可见增量过滤，取消通知生成线程。协议能力不等于真实模型工具质量。

## 入口与验证

由 [LLMClient](../client.py) 使用，本地服务启动见上级说明，不单独运行此目录文件。

```powershell
# 仓库根目录
.\.venv\Scripts\python.exe -m pytest -q tests/test_llm_backends.py tests/test_local_llm_slots.py tests/test_openai_compat.py
```

临时模型/传输替身检查协议，不下载权重、不调用远程，不作为质量分数。

HTTP payload构造器现与完整请求计量共用；本地render_chat_prompt同样由真实Token计量与生成共用，避免工具/思考参数或模板偏差。可信本地HTTP支持显式启用计数扩展，远程仍按字节门槛，详见[完整预算](../../../docs/implemented/完整Reader预算与生产引用校验.md)。
