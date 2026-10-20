# 冻结研究协议与角色

[项目首页](../../README.md) · [研究工具](../../memoria/experiments/README.md) · [运行配置](../../config/README.md)

这里保留历史LCCC检索/QA与LoCoMo风险回退实验的公开角色、预算及注册协议，原configs/research-*文件迁入时逐字节保留。它们是实验复现材料，不能拿来替代config/models.toml或宣称当前版本重新取得旧成绩。

| 文件 | 用途 |
| --- | --- |
| [本地角色](research-local-roles.toml)、[远程角色](research-remote-roles.toml) | 历史Reader/Extractor的角色参数 |
| [LCCC注册](research-retrieval-eval-20261012.json) | 固定输入、运行顺序、预算和失败分母 |
| [LoCoMo v1](research-locomo-risk-20261013.json)、[v2](research-locomo-risk-20261014.json)、[v3](research-locomo-risk-v3-20261014.json) | 各版本协议，不能混合成绩或续跑 |
| [生产机制v1](production-memory-locomo-probe-20261020.json)、[v2](production-memory-locomo-probe-v2-20261020.json) | 固定单角色原话的形成/Claim/View/撤回诊断；每轮5个自定义检查、最多8调用，非官方QA或效果消融 |

JSON中历史路径字符串保留以维持SHA；evaluate_retrieval在旧角色路径不存在时，从注册文件同目录定位同名角色。原始gold、预测、失败和费用账本仍在被忽略的data，公开聚合见[实验归档](../../assets/evaluation/README.md)。历史文件迁移不重跑成绩；2026-10-20新机制诊断的实际调用与边界见[生产闭环补测](../../docs/implemented/生产记忆闭环与逐题归因.md#claim与view真实调用补测2026-10-20)。
