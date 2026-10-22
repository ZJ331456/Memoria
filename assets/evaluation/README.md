# 当前双组全量

[项目分类表](../../README.md#locomo-全量评测) · [顺序协议](../../eval/protocols/kernel-vs-context-judge-v3-ordered-histories.json) · [完整口径](../../docs/implemented/双组内核评估与费用估算.md)

当前从Memoria原生Kernel＋同模型与完整/截断历史Baseline首轮开始。主分1–4类1540题，第5类446题独立。按完整计划分母计算；未完成不是0分实测，也不只算已评分项。运行中/停止/完整状态明确分开。

只发布无逐题原文/问题/答案的current聚合与分类图。prompt、逐题答案、gold、调用/失败/unknown、隔离SQLite仅留本机`data/users/<UUID>/experiments`，Git忽略。

旧四方法LoCoMo、LCCC和同池选择图表/聚合已删除，不作为当前结果；旧专用绘图器也已移除。[退役SHA清单](archive.manifest.json)仅用于Git追溯，不含旧分数。生产资料与原始费用/失败账本不改写。


首轮完成：Memoria35.58%主分/92.83%对抗，384执行失败；Baseline87.34%/91.48%，13执行失败，按全部计划分母。形成2424候选仅15批准、0发布巩固，未验证形成收益。详见[current.json](current.json)、[PNG](current.png)、[SVG](current.svg)、[运行时源码指纹](source-code.manifest.json)；源文件指纹按UTF-8/LF规范化，不把dirty-worktree基准commit误当全部运行代码。根README保留失败和局限，原始manifest的failed不改写为全题成功。
