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

## 新证据选择聚合

[evidence-selection-20261012.json](evidence-selection-20261012.json)为新增三预算×四选择器的12run/768题次；复用同一既有研究RRF候选、64开发问题、0新模型调用，不与本目录旧图混并。文件仅聚合指标/run与code/cases/score指纹，无原话/账号/凭据。2048字符Top-K/Coverage40/41、MMR38/41、新覆盖39/41，没有优越性证据，原图不重写。口径见[算法说明](../../implemented/条件时序抽取与预算证据选择.md)。

## 数据范围审计

[dataset-scope-20261013.json](dataset-scope-20261013.json)核对本机注册研究清单、冻结输入SHA与公开验证归档记录数，仅发布数量/来源类型/协议/指纹，无原话、账户UUID或凭据。1个LCCC子集50段/206消息，同源QA v1/v2各64题；已下载归档20000条，当前仅对子集完整64题评测。它是范围审计，不是新的模型成绩或第三个数据集。历史图表不重写。

## LoCoMo十题聚合

[locomo-smoke-20261013.json](locomo-smoke-20261013.json)是后续独立官方构建benchmark的10题真实调用聚合与指纹，无原话/答案/账号UUID/凭据，不与LCCC64题或历史图混合。10调用/0错误、4回答6拒答，候选/呈现gold证据召回34%/22%，词面分支0命中、实际偏Dense。非全量/官方F1/J/长期质量；代码dirty及1个缺失gold引用均保留。[说明](../../implemented/LoCoMo十题真实调用诊断.md)。

## LoCoMo完整1986题（2026-10-13）

[初次全分母](locomo-full-firstpass-20261013.json)保留3执行与2回退连接错误；[最终聚合](locomo-full-20261013.json)三批次继承/5题重试后最终0错误，普通1540 F1=58.65%、对抗446短语分92.38%。这不是LCCC数据或语义准确率，模型/回退/文本/许可限制见[详细说明](../../implemented/LoCoMo全量评测与英文检索修复.md)。

![LoCoMo分类结果](locomo-full-20261013.png)

[SVG](locomo-full-20261013.svg) · [来源/图片SHA](locomo-full-20261013-chart.json)。图用scripts/render_locomo_chart.py从已冻结公开聚合重绘，0私有读取/0新评分/0模型调用；不混进旧3张LCCC图的source index。
