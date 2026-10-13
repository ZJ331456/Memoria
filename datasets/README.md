# Datasets：公开来源与官方Benchmark清单

[根入口](../README.md) · [真实来源说明](../docs/implemented/真实对话数据与实验入口.md)

lccc-base.manifest.json记录LCCC公开来源版本、选样及指纹。Git仅保存清单；原话、映射、标注、inputs/gold与运行账本留在账号私有data目录。

```powershell
# 从仓库根；目标账号须已建立
.\.venv\Scripts\python.exe scripts/prepare_real_dialogues.py --limit 50 --user your_username
```

当前诊断子集50段/206原话，来源组可追溯但真实人物、事件日期和跨会话连续身份未知。归入qin或其他账号的实验空间不等于该账号本人说过这些话；导入不创建本人记忆或自动审核任务。不能补造人物ID、真实日期或长期标签，旧个人历史仍不导入。

公开可下载不等于已完成商业训练/展示/再分发权益核验。商业发布前核对原始语料、模型及依赖许可和授权范围；不把下载脚本或数据构建代码的许可证当成原话权利许可。[商用计划](../docs/plans/长期陪伴记忆研究与实验计划书.md#18-下一阶段功能优先级与商用准备)。

## 2026-10-13实际评测范围

本机注册的是1个真实来源数据集LCCC子集（50段/206消息），同源QA v1/v2各64题，v2现行；Window/Dense/RRF是三种方法，不是三个数据集。已下载验证归档有20000条非空记录，当前未全量评估原始归档；每轮现行QA完整64题，仍受合法前缀和输入预算约束。AI辅助gold与匿名短对话不能给出连续个人长期效果或Claim F1。[不含原话的清单核对](../docs/assets/evaluation/dataset-scope-20261013.json)。

## 后续LoCoMo接入

[locomo10.manifest.json](locomo10.manifest.json)记录用户指定的官方构建benchmark（LLM生成并人工编辑、CC-BY-NC-4.0）：原文件2805274字节/10对话/1986题、SHA冻结。原始副本在忽略目录data/benchmarks/locomo/raw/，与qin的50段LCCC和生产个人事实分开；inputs/gold在账号research/benchmarks，真实10题run在experiments。仅固定每段1题，未全量跑1540普通题/446对抗题。[结果与命令](../docs/implemented/LoCoMo十题真实调用诊断.md)。

LoCoMo正式入口新增locomo-run（完整1986题，1540普通/446对抗独立），源码SHA固定，清单不承载原始语料；data/benchmarks原文件、账号research/benchmarks冻结包和experiments结果分开。旧十题开发诊断不覆盖；异常gold保留，文本模式不读图像搜索字段。[完整协议](../docs/implemented/LoCoMo全量评测与英文检索修复.md)。

完整LoCoMo1986题已实测，清单full_benchmark_evaluated=true；1540普通/446对抗独立结果。文本/官方构建与自然来源LCCC分开，9标签异常主评分原样保留。[范围与结果](../docs/implemented/LoCoMo全量评测与英文检索修复.md)。
