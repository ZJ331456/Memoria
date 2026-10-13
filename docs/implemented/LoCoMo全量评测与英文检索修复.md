# LoCoMo 英文检索修复与全量评测

2026-10-13，根据十题诊断和用户全量评测要求新增独立协议。历史[十题记录](LoCoMo十题真实调用诊断.md)及其冻结输入、gold、运行产物保持原样；英文正式入口为 `locomo-run`，原 `locomo-smoke` 是v1历史复现入口。

## 实现与协议

[locomo_eval.py](../../memoria/experiments/locomo_eval.py)提供不可覆盖inputs/histories/gold冻结、原始顺序全题选择、独立评分和有界异步reader。默认full包含 **1986题**：1540普通题、446对抗题分别统计，不靠隐藏limit、答案或成功率换题。`--scope smoke`复用此前固定10题。全量已完成，最终结果见下节；初始故障与修复批次分别保存。

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

## 运行期TLS性能修复与检查点

首个全量run 20261013T113857Z-0b2e716b来自clean04efabc，完成301题时冻结为interrupted，剩余1685题未尝试保留完整分母，3个在途请求是否计费未知。没有丢弃已完成预测或再次请求这301题。全量过程中定位到HTTPX每次建client重复载入CA；本机实测初始化2.739–5.845秒，profile中3次load_verify_locations合计2.893秒，阻塞事件循环并放大Embedding墙钟。这是运行时问题，不归因给GPU或算法召回。

HTTP后端普通/流式新增可复用验证TLS上下文：保留HTTPX的SSL_CERT_FILE/SSL_CERT_DIR/certifi优先级、证书/目录变化检测、hostname与CERT_REQUIRED；冷加载在线程中。只缓存CA上下文，不缓存HTTP会话/Key/Cookie。新locomo-run --resume-from读取同账号已停止、artifact SHA一致的检查点；输入/历史/gold/规则/提示/模型资产/reader参数匹配才继承，校验源ID/SHA/引用，未完成题才调用。新run单独记录来源代码/产物、继承和新增调用，合并分数保留1986题分母；不同执行代码批次及旧在途usage未知明确披露。

TLS/后端/reader/runner相关94 passed / 29.10s，复核/继承/篡改检查19 passed / 10.32s，替身测试不作质量成绩。正式续跑及结果随后追加；原检查点和失败分母不覆盖。

TLS/续跑版本完整工程验收525 passed / 199.78s。续跑恢复后Embedding实际查询约30–76ms，原301题中3次ConnectError及2次回退ConnectError保留；目前后续调用没有新增最终case错误。新增`--resume-from RUN --retry-errors`仅对已停止run中执行或回退失败题重试，其他预测继承；多级祖先的调用/usage继续计入，原失败不覆盖，不能筛F1低题做选择性成绩。新结果仍完整1986分母，明确报首次失败和修复次数。

## 最终全量结果（2026-10-13）

**1986题最终全部执行/JSON/展示引用契约通过，最终case错误0、回退错误0。** 普通1540题与对抗446题完整分母，没有按答案/F1删题。reader为真实deepseek-flash非思考、max_tokens1024、温度0、并发4；本地英文BGE/CUDA。没有judge/extractor/生产Claim写入，不能当小模型权重能力、语义准确率或真实陪伴收益。

| 类型 | 题数 | 首次RRF分数 | 最终复核分数 |
| --- | ---: | ---: | ---: |
| 多跳 Answer F1 | 282 | 43.44% | **45.09%** |
| 时间 Answer F1 | 321 | 52.12% | **55.95%** |
| 开放域 Answer F1 | 96 | 19.55% | **22.47%** |
| 单跳 Answer F1 | 841 | 66.08% | **68.37%** |
| 普通题逐题平均 Answer F1 | 1540 | **56.12%** | **58.65%** |
| 对抗题官方拒答短语分 | 446 | 94.17% | **92.38%** |

普通题总分是对1540题逐题均值，按类别题数加权，不是四个类别均值再平均。对抗题另外统计：结构化abstain **410/446=91.93%**，answer **36/446**；官方短语规则会把含“not mentioned”的某些answer也记分，因此它不是语义拒答正确率。完整来源复核让普通F1增加2.53个百分点，却让对抗拒答短语分下降1.79个百分点，不能宣传全面改进。

**检索与选择：** 1986/1986题有英文词面贡献。普通有gold证据的1536题，候选ID召回宏平均81.13%，首次紧凑选择85.19%（邻句增加候选外来源），最终包含回退91.62%；引用ID召回75.01%。最终候选→首次呈现的gold召回下降题 **0**，旧的选择漏失已在本协议消除；这不证明所有必要语义证据都覆盖。4道开放域题没有标注证据，不强行赋召回1，仍纳入QA分母。

原固定10题最终8回答/2拒答、Answer F1=53.29%，不是80%准确率。剩余两题：健康题的体型/运动线索不能可靠推出所标注疾病；东京题的会话日期与近期访问不能确定首次出行区间。完整原话复核仍拒答，未借gold强答；十题用于开发，不能报告独立泛化收益。

**执行链：** clean04efabc的301题检查点 → cleand68a5a3续跑至1986题（初次1983成功/3执行ConnectError，另2回退ConnectError）→ cleana51f686仅重试5个工程故障题，新增8次reader、继承1981题。最终run `20261013T123154Z-c27f2463`，完整初次run `20261013T121235Z-fb31ca6e`，原检查点 `20261013T113857Z-0b2e716b`；3个停止时在途请求的计费/usage未知。不能说是同一次无中断、单一代码批次运行，也不抹掉历史错误。

计入祖先账本共 **2652次已记录reader尝试**，其中历史连接失败5次；usage已知2647次。已知prompt **36478873**、completion **93238**、total **36572111** Token；货币成本及3在途请求费用未知。全量三阶段活动墙钟合计 **2919.225秒（48.65分钟）**，不含检查点间开发/校验间隔，也不含此前十题开发调用；续跑阶段1073.610秒，故障重试47.659秒。完整病例时延混合三个批次/冷启动，p50=2.105秒、p95=27.419秒，不能当HTTP生产SLO或TLS单因素收益。中断前查询编码无法精确完整计数，运行metrics的编码字段为确认下界，原计量不回写。

原9个异常标签：6个条目可作语法定位诊断，3个无法恢复，原文/标签/主评分不变。LoCoMo有910条带图片来源，606道普通题引用带图来源；不代表都必须看图，但文本模式不宣称完整多模态复现。来源/许可与[清单](../../datasets/locomo10.manifest.json)保持。

生产库前后50会话/206消息/0memories/0reviews/0jobs；没有导入benchmark为qin个人画像。逐题问题/参考/模型回答、调用及gold只在账号reports/experiments。公开：[初次完整分母](../assets/evaluation/locomo-full-firstpass-20261013.json)、[最终汇总与指纹](../assets/evaluation/locomo-full-20261013.json)、[结果图](../assets/evaluation/locomo-full-20261013.png)。

## 新发现的质量边界与下一阶段

- **开放域推断最弱：** F1仅22.47%，普通166题仍拒答，其中164题标注ID已可见；ID可见不证明文本足够，需分文本缺失、跨度/指代、推断尺度与reader保守。不能把这些一律当工程拒答故障，或用gold生成回答。
- **对抗过答：** 完整来源复核牺牲部分拒答；下一项先登记query-only风险门控/回答支持度契约，再以同模型/预算/完整分母比较。个人明确事实与背景推断应区分，NLI模型建议不能自动批准高风险记忆；不在本轮全gold上调参后声称独立提升。
- **回答与证据语义：** 100道普通题已回答但F1=0，可能含错误、同义表达、冗余、备选答案或标注问题，未独立语义审核。下一项针对多跳覆盖、相对时间精度和严格引用支持做开发审查；同reader对照和独立留出协议需要另登记。
- **小模型预算：** 完整历史回退消耗大量上下文；本轮远程字节门槛而非可信远程精确Token窗口。1B本地部署不能照搬几十K上下文，应先做可验证紧凑证据/需求预测和同Token预算对照。
- **研究范围：** 本轮没有Claim抽取/条件/更新决策F1、真实过去知识状态或长期陪伴效果。仍待新获准连续本人资料，独立真人研究评估暂缓，生产明确审批保留。

工程全量525 passed / 199.78s（TLS/续跑版本）；之后失败恢复/嵌套账本受影响24 passed / 11.42s。官方评分源SHA与28组对照一致已记录；这些是工程验收，不能转换成研究正确率。新结果图仅重绘已冻结聚合，0新模型调用/0新评分。

费用补充：供应商明确返回缓存字段的调用2647次，prompt cache hit 28585342、miss 7893531 Token；缓存Token不能按全部未缓存价格估费，实际账单仍未知。公开分析补核验查询编码确认下界1991（原检查点301+续跑1685+失败恢复5），原运行metrics未回写，在途编码未知仍保留。

最终核对：全1986题对上游实际regex/Porter实现逐题评分一致，0模型调用/0重写历史分数；普通候选gold ID逐个核对丢失0（不只比较召回数）。全量525项和最终受影响24项均通过；本次公开汇总/图不含原话、答案、账号UUID或凭据。

发布验收：本轮14份Markdown/294个本地链接锚点、17项既有CLI/图表检查、新图help/数值/PNG-SVG SHA与重绘一致性通过，pip check通过，示例配置值保持；Git作者/提交者/实际认证ZJ331456，普通推送main。图源为公开冻结聚合，原始bench/逐题/凭据/模型不提交。

图表发布补修：Matplotlib SVG生成时带行尾空白，已在renderer统一去尾空白/写LF，图源JSON与Git实际blob SHA及PNG/SVG重绘一致性再次核验通过；原运行/评分数值不变，0模型调用。

## 首页展示与指标口径（2026-10-13）

根[README](../../README.md#locomo-全量评测)及英文首页已整理为能力、启动、当前全量结果与下一步，去除重复的旧第6节大图及第9–13节逐次追加记录。历史LCCC、十题、Reader契约和运行故障记录仍在各实现文档/计划/公开资产中保存，没有删除或改写历史实验。

首页使用方法为行、问题类别为列的分类表，数值取自冻结最终聚合；明确首次与最终是同批输出，普通Overall仅含1,540题，对抗列为拒答短语分，不能冒充F1或论文judge J。旧LCCC 64题AI辅助字面匹配与LoCoMo官方分类评分分开解释；本轮无新模型调用、无新评分、无生产数据写入。下一步仍是风险门控、多跳/开放域/时间精度、同Token预算与独立协议验证，真实个人长期效果和Claim F1未完成。

文档验收：扫描仓库66份Markdown的612个本地链接/锚点，修复标题调整后的Embedding说明回链；中英文两张表24个数值逐一对应冻结JSON，原11个环境/模型/启动命令块完整保留。冻结聚合字节与Git基线一致，diff空白/凭据模式/私有UUID检查通过；仅修改7份文档，不重复运行模型或工程全量测试。
