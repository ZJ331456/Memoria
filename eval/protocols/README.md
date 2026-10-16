# 冻结研究协议与角色

[项目首页](../../README.md) · [研究工具](../../memoria/experiments/README.md) · [运行配置](../../config/README.md)

这里保留历史LCCC检索/QA与LoCoMo风险回退实验的公开角色、预算及注册协议，原configs/research-*文件迁入时逐字节保留。它们是实验复现材料，不能拿来替代config/models.toml或宣称当前版本重新取得旧成绩。

| 文件 | 用途 |
| --- | --- |
| [本地角色](research-local-roles.toml)、[远程角色](research-remote-roles.toml) | 历史Reader/Extractor的角色参数 |
| [LCCC注册](research-retrieval-eval-20261012.json) | 固定输入、运行顺序、预算和失败分母 |
| [LoCoMo v1](research-locomo-risk-20261013.json)、[v2](research-locomo-risk-20261014.json)、[v3](research-locomo-risk-v3-20261014.json) | 各版本协议，不能混合成绩或续跑 |

JSON中历史路径字符串保留以维持SHA；evaluate_retrieval在旧角色路径不存在时，从注册文件同目录定位同名角色。原始gold、预测、失败和费用账本仍在被忽略的data，公开聚合见[实验归档](../../assets/evaluation/README.md)。本次仅迁移文件，不运行新评测。
