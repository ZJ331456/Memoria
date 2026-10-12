# 历史注册评测图表

[根README结果](../../../README.md#6-真实开发评测结果与图表) · [原始公开报告](../../implemented/DenseRRF自动评测与视图性能.md)

registered-20261012.json仅摘录公开报告的真实聚合指标。测量代码040427c、协议lccc50-codex-dev-qa-20261012-v2、批次lccc50-dense-rrf-auto-20261012-v1；12轮纯检索768题次、9轮QA576调用，每条件三轮。不混入9776e5c旧六轮，不归因于6ea773f新增Studio/选择器。

| 图 | 口径 |
| --- | --- |
| source-recall.png / .svg | 41可知题×3次的来源召回，分母123；窗口也为100% |
| qa-literal-and-citations.png / .svg | 全部192题次字面匹配及可知引用召回，失败3/5/7保留；不是人工语义正确率 |
| empty-claim-access.png / .svg | 实际账号副本0已批准Claim，各1800次核心函数p50/p95；不是HTTP或正向视图收益 |

PNG用于GitHub显示，SVG可导出。三张图已做视觉检查，无裁切或图例遮挡。figures.manifest.json保存聚合JSON/每幅图SHA和绘图库版本，复现脚本会核对注册协议及公开报告中的数值。此目录JSON/SVG固定LF换行，避免Git跨平台检出改变字节指纹。

```powershell
# 从仓库根
.\.venv\Scripts\python.exe -m pip install -r requirements-docs.txt
.\.venv\Scripts\python.exe scripts/render_eval_charts.py
.\.venv\Scripts\python.exe scripts/render_eval_charts.py --check
```

绘图0新模型调用、0新评分，不读取私有原文/gold。50匿名来源/206原话不是连续个人资料；人工语义/偏好、真实长期保持/变化、正向视图与B7质量、实际货币成本保持未知。没有从这些汇总推导用户级置信区间。
