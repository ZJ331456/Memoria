# 真实来源与人工评测

`history.py` 管理账号独立的不可覆盖来源、同意范围、人物开发/测试划分、独立人工审核及仲裁，冻结后分离模型 inputs 与 scorer gold。`runner.py` 复用 `records.py` / `roles.py`，执行原话抽取诊断及 B0 最近窗口、B2 FTS5 trigram BM25 读取基线，评分单独执行，不修改生产记忆。

标注必须声明 `annotator.kind=human/ai_assisted`。真人填写稳定去标识化 `independence_id`，AI 填 tool 且不声明真人身份；模板默认 unspecified，不能直接提交。`annotation_seconds` 是实际标注会话墙钟，AI 时间不当人工工时。test 冻结须两名独立声明的真人；不同版本 ID 不代表不同人。协议和评分保留 gold 的审核来源。2026-10-12 已冻结 `lccc50-codex-dev-qa-20261012-v2`：50 段/206 原话审核覆盖、32 条陈述、64 QA（41 可知、23 不可知），仅 AI 辅助开发 gold，真人审核暂跳过、六轮真实 QA 调用已完成。

本地管理员入口为 [research.py](../../scripts/research.py)，操作说明与格式见 [研究工具文档](../../docs/implemented/真实历史标注与评测.md)。50 段 LCCC 公开对话仅做独立来源的开发诊断，不构造个人连续经历。临时工程输入与 MockTransport 仅存在测试，不作为真实数据或成绩。

`B2_fts_compact` 是逐字分隔语料的显式对照：临时检索键/查询 NFKC 去空白，返回原始消息与证据；原版 `B2_fts` 和无命中不回退行为保留。用户已选择跳过独立真人审核，允许使用当前 AI gold 开发对照；不解除真实连续历史的长期效果数据要求。

两组各三轮、384 次真实 DeepSeek 调用已完成；逐轮结果、阶段耗时、token、引用越界和未测项见 [QA 对照结果](../../docs/implemented/真实QA对照评测结果.md)。受影响研究测试 38 passed / 22.31 s；研究原始产物留在账号私有目录，Git 只保存汇总。

## 自动前缀与视图评测

[retrieval.py](retrieval.py)按每题可见原话前缀做本地BGE完整分块Dense及compact词面/Dense RRF(k=60)，向量缓存namespace/ID/SHA绑定，绝不扩大候选范围。runner支持retrieval-only（0 reader调用、独立gold scorer）和同输入qa；原版窗口/FTS保留。

[access_eval.py](access_eval.py)只读备份实际账号库，在副本比较空/实际状态下的无缓存、显式缓存及失效读取，记录成本/等价/真实来源资格。0 Claim只能测空状态开销。固定调用编排为[evaluate_retrieval.py](../../scripts/evaluate_retrieval.py)，要求clean固定代码；[说明](../../docs/implemented/DenseRRF自动评测与视图性能.md)。人工语义与偏好暂缓，自动指标不冒充真实长期效果。
