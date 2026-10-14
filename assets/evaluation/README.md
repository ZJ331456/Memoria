# 保留评测与图表

[项目首页](../../README.md#locomo-全量评测) · [来源清单](../data-sources/README.md) · [文档](../../docs/README.md)

按实验类别保留最新完整结果；这里只整理展示资产，未重新调用模型或评分。不同来源、协议和分母分别记录。

| 保留组 | 范围 | 聚合与说明 |
| --- | --- | --- |
| LoCoMo完整四方法 · 2026-10-14 | 全1,986题；1,540普通/446对抗；同题目与声明Reader/BGE快照，预算不同 | [JSON](locomo-risk-full-20261014.json) · [协议、成本与限制](../../docs/implemented/证据充分性与风险感知回答.md) |
| LCCC固定21轮 · 2026-10-12 | 50匿名来源/206原话/64开发QA；768召回题次、576实际Reader；含5,400次空Claim访问 | [JSON](registered-20261012.json) · [报告](../../docs/implemented/DenseRRF自动评测与视图性能.md) |
| 同池证据选择 · 2026-10-12 | 三预算×四策略，复用64题与同一候选池；12run，0新模型调用 | [JSON](evidence-selection-20261012.json) · [报告](../../docs/implemented/条件时序抽取与预算证据选择.md) |

## P0四方法全量消融（2026-10-14）

![LoCoMo质量、拒答与已知Token](locomo-risk-full-20261014.png)

[SVG](locomo-risk-full-20261014.svg) · [图源与图片SHA](locomo-risk-full-20261014-chart.json)。普通F1、对抗官方拒答短语分、已知Token/题各自解释；它们不是人工语义准确率或实际账单。失败和未知usage保留，自适应组1题最终工程失败留完整分母。综合目标未达，生产默认保持；后续工程修复尚未重跑该冻结v2成绩。

原始基线的分类指标从已存最终汇总复制到同一JSON的baseline_category_metrics，未重新计算分数。原始测量的code/artifacts路径是历史代码快照，保留原路径和SHA；这些字段不作为当前资源链接。

```powershell
.\.venv\Scripts\python.exe scripts/render_locomo_risk_chart.py
.\.venv\Scripts\python.exe scripts/render_locomo_risk_chart.py --check
```

## LCCC开发诊断

| 图表 | 口径 |
| --- | --- |
| [来源召回 PNG](source-recall.png) / [SVG](source-recall.svg) | 每条件41可知题×3次，窗口也覆盖全部已知来源 |
| [字面匹配与引用 PNG](qa-literal-and-citations.png) / [SVG](qa-literal-and-citations.svg) | 每条件192题次，原失败保留；字面匹配不等于语义正确 |
| [空Claim访问 PNG](empty-claim-access.png) / [SVG](empty-claim-access.svg) | 0已批准Claim，一致性账号副本的核心函数开销；不是HTTP SLO或正向画像收益 |

图源代码040427c、注册协议和数值核对由渲染器完成；[figures.manifest.json](figures.manifest.json)记录聚合及六幅图SHA。实验不归因于后续Studio、选择器或工程修复。

```powershell
.\.venv\Scripts\python.exe scripts/render_eval_charts.py
.\.venv\Scripts\python.exe scripts/render_eval_charts.py --check
```

## 来源与精简规则

[数据范围审计](dataset-scope-20261013.json)仅说明来源/数量/指纹，不是一组模型成绩。LCCC匿名短对话不作为连续本人来源；LoCoMo是官方构建的文本研究benchmark。通用语义、真实长期效果、正向视图收益和独立真人评价仍待验证。

已移除旧十题冒烟、304题开发聚合、初次/重复全量导出、重复基线图表及旧266题派生分类。三个当前实验族之外的公开旧文件可按[精简清单](retention.manifest.json)中的Git revision、原路径和SHA追溯；原始私有run、祖先失败、费用和gold均未删除。旧六轮QA与十题诊断的独立结果文档也从当前目录移除，历史链接固定到精简前提交。
