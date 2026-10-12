# Datasets：真实公开来源清单

[根入口](../README.md) · [真实来源说明](../docs/implemented/真实对话数据与实验入口.md)

lccc-base.manifest.json记录LCCC公开来源版本、选样及指纹。Git仅保存清单；原话、映射、标注、inputs/gold与运行账本留在账号私有data目录。

```powershell
# 从仓库根；目标账号须已建立
.\.venv\Scripts\python.exe scripts/prepare_real_dialogues.py --limit 50 --user your_username
```

当前诊断子集50段/206原话，来源组可追溯但真实人物、事件日期和跨会话连续身份未知。归入qin或其他账号的实验空间不等于该账号本人说过这些话；导入不创建本人记忆或自动审核任务。不能补造人物ID、真实日期或长期标签，旧个人历史仍不导入。

公开可下载不等于已完成商业训练/展示/再分发权益核验。商业发布前核对原始语料、模型及依赖许可和授权范围；不把下载脚本或数据构建代码的许可证当成原话权利许可。[商用计划](../docs/plans/长期陪伴记忆研究与实验计划书.md#18-下一阶段功能优先级与商用准备)。
