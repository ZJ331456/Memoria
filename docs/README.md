# Memoria 文档

[项目首页](../README.md) · [展示与图表](../assets/README.md) · [来源清单](../assets/data-sources/README.md)

文档仅分为两个目录：**implemented记录已经实现和验证的行为；plans记录研究路线、目标和待验收事项。** 图片、公开实验聚合和来源清单统一放在根assets。

## 已实现

| 主题 | 入口 |
| --- | --- |
| 安装与启动 | [完整配置指南](implemented/安装配置与启动.md) · [后台启动/停止](implemented/本机后台启动与停止.md) |
| 系统与接口 | [系统架构](implemented/系统架构与实现说明.md) · [API](implemented/API接口文档.md) |
| Chat、Studio与渠道 | [前端架构](implemented/Chat与Studio前端架构.md) · [资料与记忆后台](implemented/MemoryStudio与统一记忆访问.md) · [飞书私聊](implemented/飞书机器人对话接入.md) |
| 记忆与状态 | [分层配置](implemented/记忆分层与配置.md) · [Claim/条件视图](implemented/Claim条件视图与访问路由.md) · [来源与审核](implemented/自动记忆审核与来源定位.md) |
| 检索与回答 | [条件/证据选择](implemented/条件时序抽取与预算证据选择.md) · [完整Reader预算](implemented/完整Reader预算与生产引用校验.md) · [风险核验](implemented/证据充分性与风险感知回答.md) |
| 数据与权限 | [账号隔离](implemented/用户登录与数据隔离.md) · [真实来源导入](implemented/真实对话数据与实验入口.md) · [本机清理记录](implemented/本机数据目录与残留清理.md) |
| 工程修复 | [内部一致性](implemented/内部记忆一致性修复记录.md) · [算法审查](implemented/算法逻辑审查逐项修复.md) |
| 实验与复现 | [保留的三组实验](../assets/evaluation/README.md) · [研究工具](implemented/真实历史标注与评测.md) |
| 文档与资产维护 | [当前布局与精简规则](implemented/文档结构与展示资产.md) |

全部模块与操作说明见[implemented导航](implemented/README.md)。实现、工程验证与研究效果分别陈述；接口存活、测试替身和历史指标不作为新版本效果证明。

## 待实现计划

[研究与实验计划书](plans/长期陪伴记忆研究与实验计划书.md) · [研究来源](plans/研究来源.md) · [plans导航](plans/README.md)

当前优先项：真实连续来源上的记忆演化、同预算质量/拒答/成本校准、备份恢复与迁移、账号生命周期和运行监护。LoCoMo完整四方法综合目标未达，生产默认保持；独立真人研究评价仍暂缓。

## 文档维护

按[AGENTS](../AGENTS.md)及[GitHub规范](implemented/github提交规范.md)协作。每项变更同步实现说明、计划状态和本页；资产变动需更新引用及SHA。原话、gold、个人资料、凭据与模型不提交。
