当前正式：[v3方法顺序／历史并发](kernel-vs-context-judge-v3-ordered-histories.json)，先完整Memoria再Baseline，最多8个隔离历史。v2串行前测保留本机，不混入正式质量；输入、Judge、模型与预算保持显式登记。

已中止的串行前测：[v2串行登记](kernel-vs-context-judge-v2-serial.json)先Memoria全1986题，再Baseline；全局请求/费用保护、连续两次欠费停止，完整分母与私有账本保留。v1提案原字节不变，不混称同一并发协议。

# 冻结研究协议与角色

[v15最终值绑定与日期范围修复36题](kernel-memory-judge-v15-value-closure-36.json)仍只执行Memoria一组；原36题、caption输入、模型和Judge沿用v14。依据复现缺陷修复区间参照日期误拒、未引用的物品值和来源言语行为时间边界，旧v14的13/28与失败费用私有保留，不局部挑题重试。

[v14证据流修复单组36题](kernel-memory-judge-v14-evidence-flow-36.json)仅执行完整Memoria、原36个ID，450调用/1.5美元保护；新输入显式包含188条独立机器BLIP描述，788段原对话保持，旧v13纯文本成绩不改。配对变化包含输入完整性修复，不能作为单模块因果收益；失败/未知留完整分母。

[v13最终Memoria单臂36题](kernel-memory-judge-v13-final-36.json)按用户最终要求只执行完整框架，350调用/1美元保护；原36题、DeepSeek Flash与Judge保持。它在修复通用顿号覆盖错误后独立执行，不局部重试或追认v12失败，不重复内部对照。旧v12诊断和全部调用/费用保留。

[v12原36题记忆读取诊断](kernel-vs-context-judge-v12-joint-memory-ablation-36.json)保留v11原36个ID、模型与Judge；内部原文/自适应/完整历史对照只用于定位机制，最终公开成绩仅Memoria一组。严格登记各臂开关、顺序及750调用/3美元保护，不新增全量或按分数挑选重跑。v11和旧协议原字节保持。

[双组评估v1提案](kernel-vs-context-judge-v1.json)：完整历史Baseline vs生产Kernel＋同模型，独立LLM二分类，主1540/对抗446题；本轮仅0调用估算，付费执行待预算。核对输入SHA、模型、Reader/字节预算与Judge提示SHA，不把提案当作成绩。

遗留的[20261023](locomo-deepseek-cache-20261023.json)与[20261024](locomo-deepseek-cache-20261024.json)候选缓存登记按原字节保留；其中整段子集重放、修复共用头及结构上限是未经验证的提议，不代表当前生产默认或收益。原budget-micro入口已由双组入口替代。
[DeepSeek缓存布局登记](locomo-deepseek-cache-20261022.json)允许显式比较legacy与共享Reader输入前缀，Reader输入不变，引用子集继续短请求；原v3登记不能偷偷启用。两题诊断与负向试验见[记录](../../docs/implemented/DeepSeek缓存前缀与成本审计.md)，尚无新全量成绩。




2026-10-22：[v3同题诊断登记](locomo-v3-diagnostic-20261022.json)与[完整角色](locomo-v3-roles-20261022.toml)固定并发2、标准输入SHA、Reader、英文BGE权重及评分依赖。全量风险方法必须显式登记；历史v2保持原字节，不宣称复现或未见泛化。仅完成预检，尚未跑新LoCoMo，见[13项审查](../../docs/implemented/协议计量隐私与边界审查.md)。

[项目首页](../../README.md) · [研究工具](../../memoria/experiments/README.md) · [运行配置](../../config/README.md)

这里保留历史LCCC检索/QA与LoCoMo风险回退实验的公开角色、预算及注册协议，原configs/research-*文件迁入时逐字节保留。它们是实验复现材料，不能拿来替代config/models.toml或宣称当前版本重新取得旧成绩。

| 文件 | 用途 |
| --- | --- |
| [本地角色](research-local-roles.toml)、[远程角色](research-remote-roles.toml) | 历史Reader/Extractor的角色参数 |
| [LCCC注册](research-retrieval-eval-20261012.json) | 固定输入、运行顺序、预算和失败分母 |
| [LoCoMo v1](research-locomo-risk-20261013.json)、[v2](research-locomo-risk-20261014.json)、[v3](research-locomo-risk-v3-20261014.json) | 各版本协议，不能混合成绩或续跑 |
| [生产机制v1](production-memory-locomo-probe-20261020.json)、[v2](production-memory-locomo-probe-v2-20261020.json) | 固定单角色原话的形成/Claim/View/撤回诊断；每轮5个自定义检查、最多8调用，非官方QA或效果消融 |

JSON中历史路径字符串保留以维持SHA；evaluate_retrieval在旧角色路径不存在时，从注册文件同目录定位同名角色。原始gold、预测、失败和费用账本仍在被忽略的data，公开聚合见[实验归档](../../assets/evaluation/README.md)。历史文件迁移不重跑成绩；2026-10-20新机制诊断的实际调用与边界见[生产闭环补测](../../docs/implemented/生产记忆闭环与逐题归因.md#claim与view真实调用补测2026-10-20)。
