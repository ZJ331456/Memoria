# 冻结研究协议与角色

评估对照只保留两份登记（用户决定）：首轮双组对照与最新Memoria全覆盖收尾。其余为运行时默认或测试冻结输入；被替代的全部历史登记经固定提交 [38648d3](https://github.com/ZJ331456/Memoria/tree/38648d3/eval/protocols) 追溯。原文、gold、逐题记录与调用账本仅存本机。

| 文件 | 用途 |
| --- | --- |
| [kernel-vs-context-judge-v3-ordered-histories.json](kernel-vs-context-judge-v3-ordered-histories.json) | Baseline（87.34%）与Memoria v1（35.58%）首轮双组全量 |
| [kernel-vs-context-judge-v6d-deepseek-last-28-recap.json](kernel-vs-context-judge-v6d-deepseek-last-28-recap.json) | Memoria v2（86.56%）全覆盖收尾登记（v4熔断→v6→v6b→v6d补跑链） |
| [kernel-vs-context-judge-v1.json](kernel-vs-context-judge-v1.json) | eval_command 的默认登记路径（运行时依赖；新执行必须显式 --registration 覆盖） |
| [locomo-v3-diagnostic-20261022.json](locomo-v3-diagnostic-20261022.json) / [locomo-v3-roles-20261022.toml](locomo-v3-roles-20261022.toml) / [research-locomo-risk-v3-20261014.json](research-locomo-risk-v3-20261014.json) | LoCoMo风险v3诊断链（test_audit13冻结输入） |
| [production-memory-locomo-probe-v2-20261020.json](production-memory-locomo-probe-v2-20261020.json) | 生产闭环探针登记（test_closed_loop冻结输入） |

被替代的历史登记见固定提交 38648d3；它们是实验复现材料，不能替代config/models.toml或宣称当前版本重新取得旧成绩。新评估须另行显式登记；历史成绩不改写。

[项目首页](../../README.md) · [研究工具](../../memoria/experiments/README.md) · [运行配置](../../config/README.md)
