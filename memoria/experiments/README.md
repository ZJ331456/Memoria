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

2026-10-12 既有开发协议 lccc50-codex-dev-qa-20261012-v2：50 匿名对话/206 原话/64 QA（41 可知、23 不可知），AI 辅助 gold，真实连续人物 0。此前六轮 384 次 QA 与后续固定 21 轮（768 纯召回题次、576 reader）为不同批次，不合并。后批窗口/Dense/RRF 字面匹配 166/161/158（各 192），召回补全没有带来本批字面收益。分母/来源见 [自动评测](../../docs/implemented/DenseRRF自动评测与视图性能.md)及 [此前 QA](../../docs/implemented/真实QA对照评测结果.md)。

access_eval 用真实账号副本，0 Claim 仅为空状态开销，不证明正向视图加速。Studio 只读规范 manifest/执行 metrics，核验 owner/SHA，不展示原始 gold/prompt、独立 score 或逐题钻取，不启动 runner。人工项按用户选择暂缓，真实长期历史缺口仍保留。

## 验证

```powershell
# 仓库根目录
.\.venv\Scripts\python.exe -m pytest -q tests/test_research_workflow.py tests/test_source_retrieval.py tests/test_experiment_records.py tests/test_dataset_import.py tests/test_memory_studio.py
```

隔离临时输入与故障替身只验证协议/泄漏/恢复，不充当用户历史或研究成绩。

2026-10-12新增[预算证据选择诊断](../../docs/implemented/条件时序抽取与预算证据选择.md)：原RRF候选、64开发QA、三档字符预算×四策略，12run/768题次，0新embedding/LLM调用。同池SHA一致、gold仅独立评分；2048字符Top-K/Coverage完整覆盖40/41，MMR38/41、新覆盖39/41，不切换默认策略。`run --evidence-selector`可接入新QA；生产审批和未知语义质量保留。

## 官方构建Benchmark的十题入口

`research.py --user USERNAME locomo-smoke --help`显示独立入口。普通真实历史协议不被LoCoMo替换；新授权只对指定官方文件做10题，按类别/源顺序固定，不凭gold换题。真实运行10次DeepSeek/0错误，4回答6拒答；旧compact词面均无英文命中、实际仅Dense，候选/呈现证据召回34%/22%。诊断非官方F1/J/语义准确率，完整Claim演化尚未测试。[契约与限制](../../docs/implemented/LoCoMo十题真实调用诊断.md)。
