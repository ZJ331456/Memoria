# LoCoMo 英文检索修复与全量评测

2026-10-13，根据十题诊断和用户全量评测要求新增独立协议。历史[十题记录](LoCoMo十题真实调用诊断.md)及其冻结输入、gold、运行产物保持原样；英文正式入口为 `locomo-run`，原 `locomo-smoke` 是v1历史复现入口。

## 实现与协议

[locomo_eval.py](../../memoria/experiments/locomo_eval.py)提供不可覆盖inputs/histories/gold冻结、原始顺序全题选择、独立评分和有界异步reader。默认full包含 **1986题**：1540普通题、446对抗题分别统计，不靠隐藏limit、答案或成功率换题。`--scope smoke`复用此前固定10题。全量尚在本次验收流程中，结果完成后写入本页。

1. **英文词面：** FTS5 `porter unicode61` BM25，保留空格，过滤问句停用词，OR召回，每段对话缓存索引。PrefixRetriever显式注入新分支，Dense+BM25按RRF k=60融合；旧LCCC默认compact及历史成绩保留。生产Keyword/BM25/Vector加权三路不是本协议。
2. **证据保留：** v3先按融合排名保留最多40候选，再用剩余12000字符预算补同会话前后各1条原话；上下文不能先挤掉候选。相邻来源可能在原Top40外，trace单独记录；整条原话不截断，来源去重，按原对话顺序呈现。SHA保留账本，reader只见id/speaker/原始session日期/text。实际紧凑证据JSON计预算，完整请求另外经ReaderBudget检查。
3. **语言配置：** benchmark默认本地 `model/bge-small-en-v1.5`，384维、CLS/L2/FP32、512 Token上限、英文查询指令；原中文模型512维及指纹/配置保持。后端新增明确英文small支持，分块仍核验完整源覆盖。下载只取数据/safetensors，12文件134506016字节全部核对远端SHA/size；下载manifest及实际权重/tokenizer快照在忽略目录。来源：[BAAI模型卡](https://huggingface.co/BAAI/bge-small-en-v1.5)、[ModelScope](https://www.modelscope.cn/models/BAAI/bge-small-en-v1.5)。
4. **Reader：** 依据原话、说话者和会话日期做有源推断/相对时间运算，不编精确事件日期、姓名、诊断或图像内容。简短JSON，实际展示ID校验，重复/越界引用/工具调用拒绝；未知输出 `Not mentioned.`，不强制回答。默认并发4，每题独立client/timer，用case ID命名调用编号，错误和未尝试保留完整分母。
5. **评分：** 项目独立实现标准Porter重叠，按[上游规则](https://github.com/snap-research/locomo/blob/main/task_eval/evaluation.py)区分多跳逗号子答案、开放域分号主答案、普通F1和对抗题字面拒答。普通题报告Answer F1，对抗题报告官方拒答短语率和结构化状态；失败计0。这不是语义准确率、judge J、Claim Extraction F1或长期陪伴收益。nltk仅装项目.venv，不下载语料模型。

协议 `locomo-text-english-rrf-fallback-v5-full` 在全量调用前登记代码/参数、输入/历史/gold SHA、模型资产及reader提示SHA。在线只见query和原始history，gold在推理后评分；QA category/answer/evidence、adversarial_answer、生成的observation/summary、图像搜索query排除。允许整段官方历史是回顾式benchmark协议，不宣称测量真实人物的过去知识状态。

## 六次拒答与来源异常

第一轮修复v2 run `20261013T112058Z-9fd56e65`：中文BGE+有效英文BM25，12优先组/邻句/紧凑投影，真实DeepSeek10调用、0错误、6回答4拒答。收养机构及收养年份恢复，东京题仍有候选命中、选择遗漏，继续修成v3候选优先再补邻句并采用英文BGE。v2上游口径普通Answer F1=0.523333；多个组件同时改变，不能归因于某一项或与v1未词干化诊断直接相减。

完整源核对发现 **9个异常证据条目**，原始标签全部保留。独立 `annotation-anomalies.json`仅记录合并ID/多余冒号/前导零的可定位语法诊断，不加入检索或主评分，不猜缺失来源。电影题包含无法定位的 `D`，片名仅见图像搜索字段，原话文本没有片名；文本模式可以继续拒答，不能利用搜索生成字段或gold补造证据。

剩余拒答区分检索失败、预算遗漏、文本缺失和reader保守；最终报告逐项统计。回答数增加不是正确率，引用ID合法不等于语义支持。

## 命令与文件位置

仓库根目录、已有账号/私有profile；远程会产生实际费用，每次创建新run：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-research.txt
.\.venv\Scripts\python.exe scripts/download_embedding.py --model-id BAAI/bge-small-en-v1.5 --local-dir model/bge-small-en-v1.5
# 相同十题开发诊断
.\.venv\Scripts\python.exe scripts/research.py --user qin locomo-run --scope smoke --input data/benchmarks/locomo/raw/locomo10.json --embedding-device cuda --profile deepseek --roles configs/research-remote-roles.toml --allow-remote --concurrency 4
# 正式完整分母，默认scope=full
.\.venv\Scripts\python.exe scripts/research.py --user qin locomo-run --input data/benchmarks/locomo/raw/locomo10.json --embedding-device cuda --profile deepseek --roles configs/research-remote-roles.toml --allow-remote --concurrency 4
```

原文件SHA见[清单](../../datasets/locomo10.manifest.json)。冻结包在账号research/benchmarks，manifest/cases/prompts/calls/score/anomalies/metrics在账号experiments/run_id，详细报告在账号reports。代码/清单/无原话聚合可提交；原始数据、逐题输出、凭据/UUID、模型不提交。LoCoMo为CC-BY-NC-4.0官方构建研究基准，不变为qin本人来源或商用许可。

## 验收与下一步

v2受影响回归110 passed / 42.53s。v3新增英文分词/词干、候选优先、同会话邻句、实际预算、全题分母、对抗题无answer字段、标签保留、并发编号和gold隔离；最新完整工程验收及全量结果完成后追加，不把替身测试写成研究成绩。

全量仍需同reader/Embedding/预算/评分的方法对照。十题已经用于定位开发问题，不能冒充独立泛化收益；视觉信息、开放域推断、语义支持、真实Claim/条件/变化标签及长期效用分别待验收。独立真人研究评估继续暂缓，生产审批保留。

## v4拒答复核（全量前登记）

v3 run 20261013T112702Z-4d5bf2ab：10真实调用/0错误、6回答4拒答，F1=0.432857；多数正确标签已呈现，但两道时间题仍被reader拒答。指标没有提升，原失败不覆盖。v4对首次abstain/clarify或输出契约不合法最多加1次完整同对话原文复核；触发只读模型输出，不读gold/类别/答案。完整回退包通过同ReaderBudget才调用，不静默截断，失败保留首次合法拒答与独立fallback错误；首次输出不合法且复核失败保留case失败。它是RRF+完整来源复核方法，不是纯RRF成绩；额外调用/usage/回退失败单独统计，并列首次RRF F1和证据召回，不能用全历史补全掩盖检索质量。`--fallback none`可做纯RRF对照。

新增完整工程基线520 passed / 126.65s（v3）；v4变更再验受影响范围，不能把此前全量冒称v4全量。所有独立run/dirty指纹继续留本地。

v4同十题run 20261013T113307Z-86f6d910：14次reader（4次完整原话复核）、10题最终执行成功、8回答2拒答，普通F1=0.532857、已知226135 Token、58.534秒；增加回答不等于全答对。1次复核模型给answer但引用数组为空，契约拒绝并保留首次拒答。v5因此增加最多1次同证据格式/引用重新生成，不自动补ID/削弱校验，原初次验证失败单列；普通/回退/修复调用和usage全部入账。最新受影响回归143 passed / 44.41s；v5新增修复路径再验收。

全量前v5受影响65 passed / 14.54s，新增复核/引用重新生成工程16 passed / 4.99s；原全量520为v3基线。准备登记完整1986题，正式结果单独后续提交。
