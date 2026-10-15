# Experiments：来源、标注、runner 与评分

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

[locomo_eval.py](locomo_eval.py)的新locomo-run默认完整1986题，普通1540/对抗446独立评分；英文BM25+Dense/RRF、紧凑候选优先/邻句、英文BGE、并发4独立调用编号。已冻结locomo-smoke v1结果保留；新执行使用固定拒答策略的v2，不覆盖历史。逐题产物在账号目录，生产0写入，gold只在推理后评分。[协议和命令](../../docs/implemented/LoCoMo全量评测与英文检索修复.md)。

locomo-run --resume-from RUN_ID只继承同账号已停止且SHA/输入/规则/模型/reader一致的检查点，源ID/引用重验，未完成题才调用；新run保留来源代码/产物和新增/继承成本，不能伪装单一执行版本或重复调用已完成题。

`--resume-from RUN --retry-errors`允许从已结束run仅重试执行/回退错误，递归验证祖先产物并保留所有调用usage；成功结果不重跑，不依据QA分数筛题，原失败保留。

LoCoMo原全历史协议全1986完成：最终0执行/回退错误，原三执行/两回退ConnectError和检查点保留；普通1540 F1=58.65%、对抗短语分92.38%，不是语义准确率。原十题8回答2拒答，候选选择召回下降0题；整体多批次/成本见[完整结果](../../docs/implemented/LoCoMo全量评测与英文检索修复.md)。

新P0消融：`--fallback targeted|adaptive_risk`，开发范围`--scope development`为原顺序前两段全部304题，完整范围保持full。新方法独立登记，不把完整历史送给Reader；`locomo-audit --source-run`仅离线分类/归因Token，`locomo-compare --baseline-run --runs`核验同题/同Reader/Embedding及全部成本。方法、限制与费用见[充分性与风险](../../docs/implemented/证据充分性与风险感知回答.md)，原成绩不改写。

P0 v2完整1986题对照已完成：定向57.47%普通F1/92.83%对抗短语分/7290.80已知Token每题，风险自适应51.15%/95.96%/12663.62，最终0/1工程失败留完整分母。两新run来自干净0e0df1a，工程恢复/全部祖先费用和267次中间契约拒绝保留；原首轮/全历史是同批阶段，非四次同预算或独立未见实验。综合目标未达、生产默认保持，下一步校准必要事实状态一致性与Reader+支持整段预算，详见[完整P0结论](../../docs/implemented/证据充分性与风险感知回答.md#v2完整1986题消融2026-10-14)。

v3工程协议单独登记[配置](../../configs/research-locomo-risk-v3-20261014.json)：修复SHA/2048核验输出/最多一次修复及Reader+支持整段预留进入续跑兼容检查，不与v2混跑。88项受影响测试通过；按用户要求本轮0新模型调用/0新评分，v2全量表及其1题length失败保持。下一步开发验证需新run，不因工程修复宣称F1提升。

全历史修复新协议v6将问题/全部来源/修复指令原子保留，逐次核对实际发送内容；修复超限明确失败，不移除证据后声称成功。旧评测结果保持。[修复证据](../../docs/implemented/算法逻辑审查逐项修复.md)。

新smoke v2/全量v7执行统一定稿abstain/clarify为固定未知及空引用，原始供应商输出留账本；官方评分公式和冻结历史不回写，旧检查点不能混入新策略。[修复证据](../../docs/implemented/算法逻辑审查逐项修复.md)。

[production_replay.py](production_replay.py)通过管理员CLI冻结本账号数据库，在临时副本调用生产访问路径；默认纯词面，可显式使用离线BGE，禁止LLM/远程Embedding。候选四策略可多预算0调用重放，标签只用于后验诊断，不能用于选择。[命令与边界](../../docs/implemented/生产重放与普通来源支持.md)。
