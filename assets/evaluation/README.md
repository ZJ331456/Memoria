# 评估状态与历史归档

[项目首页](../../README.md#locomo-全量评测) · [新双组协议](../../eval/protocols/kernel-vs-context-judge-v1.json) · [估算与边界](../../docs/implemented/双组内核评估与费用估算.md)

当前正式展示从新的 **Baseline（完整历史/声明截断） vs Memoria内核＋同模型** 开始，独立LLM二分类评分。用户要求先估算、暂不全量，本协议尚无模型效果结果，不填入旧F1或虚构图表。

旧LoCoMo四方法、LCCC开发组、同池选择的聚合与PNG/SVG已经从当前展示移除。[退役清单](archive.manifest.json)保留原路径、SHA与固定Git归档；原始历史、gold、失败和费用账本继续留在本机被忽略的data，不删除或改写研究记录。

## P0四方法全量消融（2026-10-14）

已退役，仅可从[固定历史版本](https://github.com/ZJ331456/Memoria/tree/1142dea16e21b14482b6e7e16cb2da4ea2b3d9ff/assets/evaluation)追溯，不是当前内核成绩。后续若需要历史图表，渲染脚本要求显式传入归档聚合路径，不默认寻找或覆盖当前结果。
