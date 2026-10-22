# Experiments：来源、标注、runner 与评分

[rerank_comparison.py](rerank_comparison.py)只对预声明的四题LoCoMo资料库来源Reader做配对诊断，同候选/字符上限、轮换顺序、全部预测后才读取gold；不运行全量，不评测个人RRF或Claim形成。[CLI与结果边界](../../docs/implemented/LLM候选重排与同预算诊断.md)。

[entity_retrieval.py](entity_retrieval.py)提供隔离的三路RRF/实体过滤/实体RRF对照，复用生产Access/Selector/Reader；[CLI](../../scripts/evaluate_entity_retrieval.py)默认0调用，8场景×3方法，同字符预算并单列实际usage。虚构夹具直接入库不测抽取，后验标签不进预测，原始结果只保存在simulation/runs；[协议与边界](../../docs/implemented/实体身份与实体召回.md#第三阶段固定预算的实体对照)。

[production_replay.py](production_replay.py)新增closed-loop：按来源前缀调用实际Kernel/形成/审核/Claim/View/Reader，默认仅验证，显式执行受调用上限约束。attribute-closed-loop只在预测冻结后读标签，保留失败及未知阶段；[完整格式与限制](../../docs/implemented/生产记忆闭环与逐题归因.md)。原LoCoMo入口和成绩不变。

[核心包](../README.md) · [研究操作](../../docs/implemented/真实历史标注与评测.md) · [研究计划](../../docs/plans/长期陪伴记忆研究与实验计划书.md)

本包在账号私有实验目录保存真实来源与不可覆盖产物。研究读取/标注/评分不修改生产记忆；公开匿名对话不得虚构稳定人物/日期或转为账号本人画像。

## 文件映射

| 文件 | 职责 |
| --- | --- |
| [history.py](history.py) | 来源/用途同意、人物划分、审核/仲裁、原文核验及 inputs/gold 分离冻结 |
| [runner.py](runner.py) | extract/qa/retrieval-only、严格答案/引用、全部失败分母、回放恢复和独立评分 |
| [retrieval.py](retrieval.py) | 每题可见前缀的 BGE Dense、compact 词面/RRF，ID/SHA/namespace 绑定缓存 |
| [records.py](records.py) | 代码/dirty 指纹、环境/模型/数据 SHA、阶段/usage 与产物账本 |
| [roles.py](roles.py) | reader/extractor/decider/judge 参数与实际调用 |
| [access_eval.py](access_eval.py) | 实际账号一致副本的 Claim 访问/缓存/失效成本和来源资格 |
| [locomo.py](locomo.py) | 官方SHA、固定10题bundle、原文/说话者/日期、来源检索/选择/reader与独立gold诊断；不写生产 |
| [evidence_eval.py](evidence_eval.py) | 复用真实已捕获前缀候选池，四选择器同池/预算，独立gold评分，不重复调用模型 |

## 本地 CLI

仓库根目录，USERNAME 替换为已有账号：

```powershell
.\.venv\Scripts\python.exe scripts/research.py --user USERNAME --help
.\.venv\Scripts\python.exe scripts/research.py --user USERNAME run --help
.\.venv\Scripts\python.exe scripts/research.py --user USERNAME benchmark-access --help
.\.venv\Scripts\python.exe scripts/research.py --user USERNAME select-evidence --help
.\.venv\Scripts\python.exe scripts/evaluate_retrieval.py --help
```

获准来源 → 标注模板/提交 → 独立审核或明确 AI 辅助开发 → 冻结 → reader 仅见 inputs/历史前缀 → scorer 单独见 gold。格式/参数见研究文档；远程研究调用须显式 --allow-remote。help 不执行模型。

B2_fts 无命中不暗退窗口，B2_fts_compact 只规范化临时检索键、保留原文。Dense 完整分块只见当前题前缀，跨题缓存不扩大历史，RRF 参数/范围进账本。retrieval-only 无 reader，qa 另做真实回答。

## 标注和统计边界

标注声明 human/ai_assisted；真人用去标识化 independence_id，AI 声明 tool，不把不同版本号当独立人。模板 unspecified 不能提交，test 冻结需两名独立声明真人及仲裁；AI 墙钟不当人工工时。

未提交/不可用人工评分保持 human_correct=null；失败进入 status/字面分母，不伪造人工 False。保留失败、未知 usage 与新评分修订，不覆盖旧运行。字面匹配/引用召回不等于语义质量、偏好遵守或长期效果。

2026-10-12 既有开发协议 lccc50-codex-dev-qa-20261012-v2：50 匿名对话/206 原话/64 QA（41 可知、23 不可知），AI 辅助 gold，真实连续人物 0。此前六轮 384 次 QA 与后续固定 21 轮（768 纯召回题次、576 reader）为不同批次，不合并。后批窗口/Dense/RRF 字面匹配 166/161/158（各 192），召回补全没有带来本批字面收益。分母/来源见 [自动评测](../../docs/implemented/DenseRRF自动评测与视图性能.md)及 [此前 QA](https://github.com/ZJ331456/Memoria/blob/05f9fb2ac08b0c8b37a359dd3e53b9cb4abedc7d/docs/implemented/真实QA对照评测结果.md)。

access_eval 用真实账号副本，0 Claim 仅为空状态开销，不证明正向视图加速。Studio 只读规范 manifest/执行 metrics，核验 owner/SHA，不展示原始 gold/prompt、独立 score 或逐题钻取，不启动 runner。人工项按用户选择暂缓，真实长期历史缺口仍保留。

## 验证

```powershell
# 仓库根目录
.\.venv\Scripts\python.exe -m pytest -q tests/test_research_workflow.py tests/test_source_retrieval.py tests/test_experiment_records.py tests/test_dataset_import.py tests/test_memory_studio.py
```

隔离临时输入与故障替身只验证协议/泄漏/恢复，不充当用户历史或研究成绩。

2026-10-12新增[预算证据选择诊断](../../docs/implemented/条件时序抽取与预算证据选择.md)：原RRF候选、64开发QA、三档字符预算×四策略，12run/768题次，0新embedding/LLM调用。同池SHA一致、gold仅独立评分；2048字符Top-K/Coverage完整覆盖40/41，MMR38/41、新覆盖39/41，不切换默认策略。`run --evidence-selector`可接入新QA；生产审批和未知语义质量保留。

## 官方构建Benchmark的十题入口

`research.py --user USERNAME locomo-smoke --help`显示独立入口。普通真实历史协议不被LoCoMo替换；新授权只对指定官方文件做10题，按类别/源顺序固定，不凭gold换题。真实运行10次DeepSeek/0错误，4回答6拒答；旧compact词面均无英文命中、实际仅Dense，候选/呈现证据召回34%/22%。诊断非官方F1/J/语义准确率，完整Claim演化尚未测试。[契约与限制](https://github.com/ZJ331456/Memoria/blob/05f9fb2ac08b0c8b37a359dd3e53b9cb4abedc7d/docs/implemented/LoCoMo十题真实调用诊断.md)。

## LoCoMo英文全量入口

[locomo_eval.py](locomo_eval.py)支持完整1986题，普通1540/对抗446分别评分，使用英文BM25+Dense/RRF和紧凑原话。旧v1/v2结果保持。当前风险方法为v3，全量必须显式传[诊断登记](../../eval/protocols/locomo-v3-diagnostic-20261022.json)、角色、并发2和英文BGE；模型/权重/输入/评分漂移会在调用前失败，不能称复现v2。未登记基线仍默认并发4、风险开发默认2；新四方法登记均为2。[当前命令与边界](../../docs/implemented/协议计量隐私与边界审查.md)。逐题产物仅在私有研究目录，gold仅后验评分。

locomo-run --resume-from RUN_ID只继承同账号已停止且SHA/输入/规则/模型/reader一致的检查点，源ID/引用重验，未完成题才调用；新run保留来源代码/产物和新增/继承成本，不能伪装单一执行版本或重复调用已完成题。

`--resume-from RUN --retry-errors`允许从已结束run仅重试执行/回退错误，递归验证祖先产物并保留所有调用usage；成功结果不重跑，不依据QA分数筛题，原失败保留。

LoCoMo原全历史协议全1986完成：最终0执行/回退错误，原三执行/两回退ConnectError和检查点保留；普通1540 F1=58.65%、对抗短语分92.38%，不是语义准确率。原十题8回答2拒答，候选选择召回下降0题；整体多批次/成本见[完整结果](../../docs/implemented/LoCoMo全量评测与英文检索修复.md)。

新P0消融：`--fallback targeted|adaptive_risk`，开发范围`--scope development`为原顺序前两段全部304题，完整范围保持full。新方法独立登记，不把完整历史送给Reader；`locomo-audit --source-run`仅离线分类/归因Token，`locomo-compare --baseline-run --runs`核验同题/同Reader/Embedding及全部成本。方法、限制与费用见[充分性与风险](../../docs/implemented/证据充分性与风险感知回答.md)，原成绩不改写。

P0 v2完整1986题对照已完成：定向57.47%普通F1/92.83%对抗短语分/7290.80已知Token每题，风险自适应51.15%/95.96%/12663.62，最终0/1工程失败留完整分母。两新run来自干净0e0df1a，工程恢复/全部祖先费用和267次中间契约拒绝保留；原首轮/全历史是同批阶段，非四次同预算或独立未见实验。综合目标未达、生产默认保持，下一步校准必要事实状态一致性与Reader+支持整段预算，详见[完整P0结论](../../docs/implemented/证据充分性与风险感知回答.md#v2完整1986题消融2026-10-14)。

v3工程协议单独登记[配置](../../eval/protocols/research-locomo-risk-v3-20261014.json)：修复SHA/2048核验输出/最多一次修复及Reader+支持整段预留进入续跑兼容检查，不与v2混跑。88项受影响测试通过；按用户要求本轮0新模型调用/0新评分，v2全量表及其1题length失败保持。下一步开发验证需新run，不因工程修复宣称F1提升。

全历史修复新协议v6将问题/全部来源/修复指令原子保留，逐次核对实际发送内容；修复超限明确失败，不移除证据后声称成功。旧评测结果保持。[修复证据](../../docs/implemented/算法逻辑审查逐项修复.md)。

新smoke v2/全量v7执行统一定稿abstain/clarify为固定未知及空引用，原始供应商输出留账本；官方评分公式和冻结历史不回写，旧检查点不能混入新策略。[修复证据](../../docs/implemented/算法逻辑审查逐项修复.md)。

[production_replay.py](production_replay.py)通过管理员CLI冻结本账号数据库，在临时副本调用生产访问路径；默认纯词面，可显式使用离线BGE，禁止LLM/远程Embedding。候选四策略可多预算0调用重放，标签只用于后验诊断，不能用于选择。[命令与边界](../../docs/implemented/生产重放与普通来源支持.md)。

[micro_budget.py](micro_budget.py)提供budget-micro：固定官方前两题，默认四策略×三字节预算零推理，显式Reader最多4次、输出512/请求12000字节、0重试/回退；失败保留分母和未知usage。[实际诊断与边界](../../docs/implemented/生产重放与普通来源支持.md#5-两题同预算诊断与硬调用上限)。

production_replay的`prepare-memory-probe`准备固定单角色LoCoMo原文，接原closed-loop实际执行形成/审核/Claim/View/Reader与撤回；默认准备/验证0调用，显式执行另受调用预算约束。v1普通记忆与v2 Claim两轮共6次真模型调用/4829 tokens，原始记录和后验AI标注留私有空间，不是新的LoCoMo评分。统计区分状态查询尝试、选入Claim修订、编译View与热命中；合理非回答只有显式后验标签才标正确。[命令、结果与限制](../../docs/implemented/生产记忆闭环与逐题归因.md#claim与view真实调用补测2026-10-20)。

[simulation.py](simulation.py)提供用户授权的虚构功能验收，复用production_replay原生产组件，显式synthetic_functional及专用输出隔离、全调用上限、后验断言/失败分母、冻结SHA与跨版本累计报告；真实LLM、离线BGE和工程替身分层，不进入正式研究成绩。[输入与命令](../../simulation/README.md)。

2026-10-21：[生产与实验入口统一](../../docs/implemented/统一内核入口与存储边界.md)。production_replay的冻结账号副本也构造Kernel且禁用Markdown写出；entity_retrieval的Reader直接调用Kernel.answer并显式关闭自适应。rerank_comparison保留冻结候选池这一对照条件；独立PrefixRetriever LoCoMo协议及旧分数未改变。

2026-10-22新增[deepseek_cache.py](deepseek_cache.py)：`deepseek-cache-audit`按本run或显式祖先只读真实缓存usage、保留未知并估算已知用量；`deepseek-cache-probe`默认预检、显式执行至多16调用，固定两题配对、原始失败和所有成本保留。LoCoMo新缓存布局须显式`--deepseek-cache`及单独登记，旧v3不可默许。生产与隔离布局不同，Reader保持原输入；详细数字及负向结果见[DeepSeek缓存说明](../../docs/implemented/DeepSeek缓存前缀与成本审计.md)。OpenAI/Anthropic仅TODO，未实现。
