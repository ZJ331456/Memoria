# 真实来源与人工评测

`history.py` 管理账号独立的不可覆盖来源、同意范围、人物开发/测试划分、独立人工审核及仲裁，冻结后分离模型 inputs 与 scorer gold。`runner.py` 复用 `records.py` / `roles.py`，执行原话抽取诊断及 B0 最近窗口、B2 FTS5 trigram BM25 读取基线，评分单独执行，不修改生产记忆。

本地管理员入口为 [research.py](../../scripts/research.py)，操作说明与格式见 [研究工具文档](../../docs/implemented/真实历史标注与评测.md)。50 段 LCCC 公开对话仅做独立来源的开发诊断，不构造个人连续经历。临时工程输入与 MockTransport 仅存在测试，不作为真实数据或成绩。
