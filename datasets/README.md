# Datasets：公开来源与官方Benchmark清单

[根入口](../README.md) · [真实来源说明](../docs/implemented/真实对话数据与实验入口.md)

lccc-base.manifest.json记录LCCC公开来源版本、选样及指纹。Git仅保存清单；原话、映射、标注、inputs/gold与运行账本留在账号私有data目录。

```powershell
# 从仓库根；目标账号须已建立
.\.venv\Scripts\python.exe scripts/prepare_real_dialogues.py --limit 50 --user your_username
```

当前诊断子集50段/206原话，来源组可追溯但真实人物、事件日期和跨会话连续身份未知。归入qin或其他账号的实验空间不等于该账号本人说过这些话；导入不创建本人记忆或自动审核任务。不能补造人物ID、真实日期或长期标签，旧个人历史仍不导入。

公开可下载不等于已完成商业训练/展示/再分发权益核验。商业发布前核对原始语料、模型及依赖许可和授权范围；不把下载脚本或数据构建代码的许可证当成原话权利许可。[商用计划](../docs/plans/长期陪伴记忆研究与实验计划书.md#18-下一阶段功能优先级与商用准备)。

## LCCC开发诊断范围

本机注册的是1个真实来源数据集LCCC子集（50段/206消息），同源QA v1/v2各64题，v2现行；Window/Dense/RRF是三种方法，不是三个数据集。已下载验证归档有20000条非空记录，当前未全量评估原始归档；每轮现行QA完整64题，仍受合法前缀和输入预算约束。AI辅助gold与匿名短对话不能给出连续个人长期效果或Claim F1。[不含原话的清单核对](../docs/assets/evaluation/dataset-scope-20261013.json)。

## LoCoMo-10：主benchmark与完整消融

[locomo10.manifest.json](locomo10.manifest.json)记录官方构建的研究benchmark（LLM生成并人工编辑、CC-BY-NC-4.0）：原文件2805274字节、10对话/5882原话/1986题、SHA冻结。普通1540题、对抗446题分开计分；它不是qin的50段LCCC，也不是获准连续本人历史。

原始副本在忽略的data/benchmarks/locomo/raw/，账号research/benchmarks保存冻结inputs/histories/gold，experiments保存逐题结果与调用，Git仅存来源清单、公开指标和指纹。异常标签原样保留，全文本模式不读图像搜索/视觉字段。

原RRF首次/全历史复核已完成全1986题，[历史结果](../docs/implemented/LoCoMo全量评测与英文检索修复.md)保持；P0定向和充分性/风险自适应另各完成全量执行，最后0/1工程失败保留分母和全部恢复成本。[四方法聚合](../docs/assets/evaluation/locomo-risk-full-20261014.json) · [实现与未达目标](../docs/implemented/证据充分性与风险感知回答.md)。304题开发范围和10题冒烟只用于开发/协议验证，不冒充全量。
