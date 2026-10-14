# LoCoMo 十题真实调用诊断

2026-10-13，用户提供项目父目录的 locomo10.json 并要求先测10题。大小2805274字节、SHA 79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4，与已核对的[官方文件](https://github.com/snap-research/locomo/blob/main/data/locomo10.json)一致。全文件10段长对话、1986 QA；1540普通题与446对抗题分开，本轮仅10题。原始文件保留，副本在忽略目录data/benchmarks/locomo/raw/，Git只保留[清单](../data-sources/locomo10.manifest.json)和不含原话的[聚合诊断](../assets/evaluation/locomo-smoke-20261013.json)。

这是模型生成、人工编辑的官方构建研究基准，按其[CC-BY-NC-4.0许可](https://github.com/snap-research/locomo/blob/main/LICENSE.txt)保留来源，不能冒充qin真人历史或商业数据授权。本轮按用户新要求接入指定文件，不自行生成对话、人物或标签，既有LCCC/旧历史协议不覆盖。

## 已实现的最小入口

[locomo.py](../../memoria/experiments/locomo.py)复用项目PrefixRetriever、分块BGE、Evidence Selector、完整ReaderBudget与ExperimentLLM账本。CLI在[research.py](../../scripts/research.py)新增locomo-smoke；固定每段对话1题，按原顺序在类别1/2/3/4循环选择第一题，3/3/2/2分布。选题不读取答案、证据标签、reader成功率，不换题以提高成绩；不是随机或代表性样本。

```powershell
# 项目根目录；当前用户已授权本次10题调用。再次执行会创建新run并产生模型调用。
.\.venv\Scripts\python.exe scripts/research.py --user qin locomo-smoke --input data/benchmarks/locomo/raw/locomo10.json --baseline B2_rrf --embedding-device cuda --profile deepseek --roles configs/research-remote-roles.toml --allow-remote
# 只显示命令，不调用模型
.\.venv\Scripts\python.exe scripts/research.py --user qin locomo-smoke --help
```

benchmark历史保存在账号独立research/benchmarks的不可覆盖bundle，inputs/gold分文件，各自SHA冻结；没有注册成真实人物历史，没有生产记忆/Claim/任务写入。账号/profile路径与已有研究CLI同边界，旧归档和他人私密目录排除。公开原话的检索投影为speaker加原文，原文和其SHA仍单独保留；reader看到原文、说话者与原始会话日期，不补时区或事件日期。只处理文本，没有下载图片/补图像描述；observation、session_summary、event_summary、QA答案/证据/类别均不作为在线证据。

本轮是研究来源检索、选择、reader测试，不调用生产Claim/View路径，也未运行结构化抽取。输出严格JSON，引用须属于实际呈现ID，重复/未知ID及工具调用拒绝；完整请求包含系统、问题、证据、引用目录、生成参数与输出预留。来源只来自当前对话，gold在推理完成后由评分侧读取。执行失败、未尝试、usage未知和gold问题保留。

## 真实结果

run_id为20261013T104634Z-6918fed9，基于08387cf加记录中的dirty/untracked指纹；聚合保存执行源码、输入、逐题/调用/评分SHA，不宣称是干净提交实验。reader为账号私有profile中的deepseek-flash、非思考、并发1、max_tokens=1024、重试0；Embedding为本机BGE-small-zh-v1.5/CUDA。没有额外judge或extractor调用。

| 项目 | 实际结果 |
| --- | --- |
| 执行/JSON/引用契约通过 | **10/10**；错误0，reader实际调用10次 |
| 模型输出状态 | answer 4、abstain 6；这不是40%正确率 |
| Top-40候选gold证据召回宏平均 | 34% |
| Coverage最终Top-12呈现证据召回宏平均 | 22%；全部已标注必要证据完整呈现1/10题 |
| 来源索引 | 5882原话投影实际编码、10次查询编码；原文完整分块，无gold索引 |
| 完整请求 | 6653–7459 UTF-8字节；证据预算12000字符；remote精确Token窗口未知 |
| usage | 10/10已知；prompt 22834、completion 472、total 23306；货币成本未知 |
| 墙钟 | **124.801秒**，含首次模型/索引/reader；单题p50 10.644秒、p95 20.808秒，非生产SLO |
| 原始gold异常 | 1个引用ID无法在对应对话定位；原标签、原问题与分母保留 |
| 诊断词面指标 | 未词干化Token F1宏平均约0.069714；非官方LoCoMo F1、非语义准确率 |
| 官方J/独立语义评分/真实长期效果 | 未测，保持未知 |

**词面分支限制已明确查出：** 沿用的LCCC compact FTS会去掉英文查询空格，整串查询没有命中；10题lexical_rank均为空。因此本轮B2_rrf实际仅靠Dense，不能宣传完成了有效两路RRF对照，也不与生产Keyword/BM25/Vector三路加权RRF混称。中文BGE用于英文也是配置局限，但本轮没有语言模型消融，不能将所有错误归因给它。

**选择阶段有可见漏失：** conv-44-q0的gold证据进入Top-40，却未进入最终Top-12；conv-49-q0候选召回40%、最终20%。这是本次轨迹诊断，不说明扩大K或换选择器一定提升QA。只认gold ID也会漏掉可能的替代支持来源；缺失标签、词面答案长短/单复数、部分正确和额外内容均需另做语义评分。

生产sessions/messages/memories/reviews/jobs计数前后为50/206/0/0/0，没有修改qin历史或生成画像。逐题问题、参考答案、模型答复和来源ID保存在账号reports/locomo-smoke-20261013.md；调用/原始prompt/gold留在忽略目录，不发布原话与凭据。

## 工程验收与下一步

相关23 passed / 7.37s；最终受影响范围**98 passed / 59.04s**（LoCoMo、研究工作流、来源检索、账本与Reader契约），临时测试输入不作为研究数据。本轮不需要升级/污染base，不下载新模型。

12份变更Markdown的214个本地链接/锚点、16项CLI/历史图表检查、pip check及diff检查通过，示例配置值未改。公开聚合只含数量/参数/代码与产物指纹；原始文件、逐题答案/调用、账号UUID和凭据留在忽略目录，按既有main/ZJ331456同步约定执行普通推送。

下一步先登记新英文协议，修复词面分支的空格/分词，比较同一来源的英文或多语言Embedding；在冻结的候选/预算下比较Top-K/Coverage。不能在这10题gold上调参后冒充独立收益，不能覆盖本轮失败或复用run ID。再按用户后续要求逐步扩大普通1540题与独立446题，接入上游分类型F1/J和同reader/Embedding/评分的对照。本轮仅完成10题能否跑通的请求；完整LoCoMo、Claim演化F1与真实陪伴效果尚未评测。
