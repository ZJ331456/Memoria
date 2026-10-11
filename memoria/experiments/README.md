# 真实来源与人工评测

`history.py` 管理账号独立的不可覆盖来源、同意范围、人物开发/测试划分、独立人工审核及仲裁，冻结后分离模型 inputs 与 scorer gold。`runner.py` 复用 `records.py` / `roles.py`，执行原话抽取诊断及 B0 最近窗口、B2 FTS5 trigram BM25 读取基线，评分单独执行，不修改生产记忆。

标注必须声明 `annotator.kind=human/ai_assisted`。真人填写稳定去标识化 `independence_id`，AI 填 tool 且不声明真人身份；模板默认 unspecified，不能直接提交。`annotation_seconds` 是实际标注会话墙钟，AI 时间不当人工工时。test 冻结须两名独立声明的真人；不同版本 ID 不代表不同人。协议和评分保留 gold 的审核来源。2026-10-12 已冻结 `lccc50-codex-dev-qa-20261012-v2`：50 段/206 原话审核覆盖、32 条陈述、64 QA（41 可知、23 不可知），仅 AI 辅助开发 gold，真人审核及真实 QA 调用仍待开展。

本地管理员入口为 [research.py](../../scripts/research.py)，操作说明与格式见 [研究工具文档](../../docs/implemented/真实历史标注与评测.md)。50 段 LCCC 公开对话仅做独立来源的开发诊断，不构造个人连续经历。临时工程输入与 MockTransport 仅存在测试，不作为真实数据或成绩。
