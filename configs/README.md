# Configs：公开研究参数

[根入口](../README.md) · [研究工具](../scripts/README.md) · [评测结果](../docs/implemented/DenseRRF自动评测与视图性能.md)

| 文件 | 内容 |
| --- | --- |
| research-local-roles.toml | 本地模型角色参数，需已有模型与设备 |
| research-remote-roles.toml | 历史远程reader/extractor/decider/judge角色配置；不含Key |
| research-retrieval-eval-20261012.json | 固定21轮、64题、输入/gold/history指纹、调用顺序与576次分母 |

协议是研究登记，不能当作生产模型配置。私密config.toml、账号models.override.toml及DPAPI profile均在忽略路径。原始输入/gold不在此目录；克隆公开协议不能自动得到私有实验产物。

历史图表对应clean040427c。新增实验使用新evaluation_id和相应代码/数据/模型指纹，不覆盖已有登记与预测；人工评估暂缓、未知保持null，不用AI标签冒充独立真人gold。现有编排器已拒绝重复批次，带allow-remote的执行可能付费。[计划与验收](../docs/plans/长期陪伴记忆研究与实验计划书.md)。
