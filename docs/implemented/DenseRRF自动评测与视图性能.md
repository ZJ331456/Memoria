# 已实现：Dense/RRF自动评测与视图访问基准

更新：2026-10-12。按用户要求完成无需人工介入的固定协议来源召回、真实reader QA和账号空状态访问基准。人工语义评分、独立真人审核和陪伴偏好暂缓。真实连续人物=0，长期保持/真实变化/越用越熟悉的效果未测。

## 本轮结果与范围

12轮纯检索共768题次；9轮真实QA共576次DeepSeek reader调用。每种条件三轮、同一冻结64题（41可知/23不可知）、50匿名来源分组/206原话；不是50名连续用户。AI gold来源不改，没有新人工评分，字面匹配不是语义正确率。

| 来源检索 | 轮数/题次 | 可知证据召回 | 空上下文 | 检索p50/p95（ms） |
| --- | --- | --- | --- | --- |
| B0_recent | 3/192 | 100.00%（123可知题次） | 0/192 | 0.003/0.003 |
| B2_fts_compact | 3/192 | 36.59%（123可知题次） | 81/192 | 0.537/0.676 |
| B2_dense | 3/192 | 100.00%（123可知题次） | 0/192 | 11.433/38.011 |
| B2_rrf | 3/192 | 100.00%（123可知题次） | 0/192 | 12.062/18.403 |

Dense/RRF补足了compact词面基线的召回缺口；窗口也覆盖全部已知证据，不能称为长期检索优于窗口。来源均为短对话：可知题按最远证据消息距离分组，0–1距离72题次、2–3距离48题次、4+仅3题次（1道题×3）。事件日期/跨会话人物不可核验，不把这个消息间隔当长时间保持。首次Dense包含权重/GPU加载，其他轮复用已载入编码器；每轮文档向量缓存独立。百分位不反映极端冷启动，逐轮/逐阶段原始墙钟另留存。

| 真实QA | 字面匹配 | 可知/不可知匹配数 | 失败 | 可知引用召回 | 整题p50/p95（ms） |
| --- | --- | --- | --- | --- | --- |
| B0_recent | 166/192（86.46%） | 97/123；69/69 | 3/192 | 92.28% | 8961.774/11243.812 |
| B2_dense | 161/192（83.85%） | 93/123；68/69 | 5/192 | 88.21% | 8192.151/12908.7 |
| B2_rrf | 158/192（82.29%） | 91/123；67/69 | 7/192 | 87.40% | 8596.123/10744.898 |

所有失败保留在192题次/组的分母，不重试替换预测。全部可知证据呈现不保证答案/引用正确；当前字面指标仍受开发gold有限答案变体影响。人工正确性、主观效用和现实事实核验为null。Dense/RRF QA同时重跑窗口，旧六轮compact QA不作为本轮同步对照。

## 固定协议与自动接线

运行代码为clean `040427c386d989d20e1afdea380a83276e707c9c`；注册SHA `bda2637f13386ed16c06760aa01db0dc38653af65670c08ad63acdc8cf0def29`。固定文件：[协议](https://github.com/ZJ331456/Memoria/blob/38648d3/eval/protocols/research-retrieval-eval-20261012.json)。每轮数据/代码/模型/参数/产物SHA、所有case顺序和成本已核验。

BGE中文small v1.5原话完整tokenizer分块，父消息最佳块余弦；query使用生产中文instruction。RRF融合Dense top8与compact FTS/BM25 top8，k=60、父消息去重，原顺序解同分；最终至多8条完整原话、12000序列化字符。无根据gold调出的阈值。namespace/ID/原文SHA绑定向量缓存，候选始终限制于每题可见前缀，其他题缓存中的未来消息不能召回。

BGE本地CUDA/512维/FP32，权重SHA `354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026`，namespace `embedding-local:fb2b88ca2ec04dc58274575b1e74358195015e24f8ecc3e98c1a8c5b32f9de65`。设备RTX 4060 Laptop 8188 MiB；Python3.13.5、torch2.9.0+cu126、transformers5.5.3。`--embedding-device auto`仅本次运行直读已有权重，未改生产HTTP配置/base环境。GPU/CPU资源峰值未采样。

Reader为deepseek-flash非思考模式，温度0/top_p1、1024输出、无重试、并发1。服务端权重revision/供应商缓存行为未知，不能保证每次复现相同答案；远程结果不代表本地 Qwen 配置的效果。原始prompt/response/error/usage仅在账号独立实验目录，不进Git。本批040427c实验时，生产memory-access尚未接入Dense补检；后续6ea773f已实现普通记忆RRF来源补检，见[统一访问](MemoryStudio与统一记忆访问.md)，其质量不由本批成绩证明。生产Claim仍与匿名来源分域。

```powershell
.\.venv\Scripts\python.exe scripts/research.py --user qin run --mode retrieval --protocol lccc50-codex-dev-qa-20261012-v2 --baseline B2_rrf --embedding-device auto
.\.venv\Scripts\python.exe scripts/research.py --user qin score --run <实际run_id>
.\.venv\Scripts\python.exe scripts/evaluate_retrieval.py --user qin --allow-remote
```

固定批次已经运行，编排器拒绝覆盖已有evaluation_id；再次运行需登记新ID/代码与参数，不覆盖当前报告。`retrieval`模式0 reader调用，独立score才读gold。编排器在每轮前拒绝代码/输入/模型指纹或分母变化。

## 逐轮记录与实际成本

| mode/组 | run_id（UTC） | 结果 | 字面正确数 | 墙钟（s） |
| --- | --- | --- | --- | --- |
| retrieval/B0_recent | 20261012T061414Z-cde0230a | {'success': 64} | 不调用reader | 0.805 |
| retrieval/B2_fts_compact | 20261012T061415Z-28879fab | {'empty': 27, 'success': 37} | 不调用reader | 0.570 |
| retrieval/B2_dense | 20261012T061416Z-0f459127 | {'success': 64} | 不调用reader | 9.309 |
| retrieval/B2_rrf | 20261012T061426Z-e599965a | {'success': 64} | 不调用reader | 1.224 |
| retrieval/B2_rrf | 20261012T061428Z-aac9e792 | {'success': 64} | 不调用reader | 1.236 |
| retrieval/B2_dense | 20261012T061430Z-36a9f981 | {'success': 64} | 不调用reader | 1.199 |
| retrieval/B2_fts_compact | 20261012T061431Z-2ec8376a | {'empty': 27, 'success': 37} | 不调用reader | 0.518 |
| retrieval/B0_recent | 20261012T061432Z-8638de80 | {'success': 64} | 不调用reader | 0.519 |
| retrieval/B2_dense | 20261012T061434Z-b2339063 | {'success': 64} | 不调用reader | 2.009 |
| retrieval/B0_recent | 20261012T061436Z-1f9d54cb | {'success': 64} | 不调用reader | 0.495 |
| retrieval/B2_rrf | 20261012T061437Z-da5d9727 | {'success': 64} | 不调用reader | 1.435 |
| retrieval/B2_fts_compact | 20261012T061439Z-e74c9434 | {'empty': 27, 'success': 37} | 不调用reader | 0.592 |
| qa/B0_recent | 20261012T061440Z-971cc8cb | {'error': 1, 'success': 63} | 55/64 | 504.573 |
| qa/B2_dense | 20261012T062305Z-4419de02 | {'success': 64} | 55/64 | 378.496 |
| qa/B2_rrf | 20261012T062924Z-847c5ca2 | {'error': 1, 'success': 63} | 54/64 | 335.225 |
| qa/B2_dense | 20261012T063500Z-96a7098f | {'error': 2, 'success': 62} | 54/64 | 568.442 |
| qa/B2_rrf | 20261012T064430Z-8282655c | {'error': 4, 'success': 60} | 51/64 | 588.344 |
| qa/B0_recent | 20261012T065419Z-568070b7 | {'error': 1, 'success': 63} | 55/64 | 586.131 |
| qa/B2_rrf | 20261012T070406Z-7c76abc2 | {'error': 2, 'success': 62} | 53/64 | 560.236 |
| qa/B0_recent | 20261012T071327Z-c1b734fc | {'error': 1, 'success': 63} | 56/64 | 571.813 |
| qa/B2_dense | 20261012T072259Z-e92b074f | {'error': 3, 'success': 61} | 52/64 | 579.817 |

| QA组 | usage已知 | prompt/completion/total token | 可见文本TTFT p50/p95（ms） | reader p50/p95（ms） |
| --- | --- | --- | --- | --- |
| B0_recent | 192/192 | 113085/4994/118079 | 8641.04/10250.139 | 8956.314/11236.313 |
| B2_dense | 191/192 | 112711/5032/117743 | 7736.847/11514.366 | 8093.328/12779.413 |
| B2_rrf | 191/192 | 112580/5100/117680 | 8340.156/9762.33 | 8561.861/10661.747 |

TTFT记录实际首个可见流式文本，不是供应商服务端首token。case/检索/embedding/reader等嵌套墙钟包含重叠，不能相加为总耗时。实际货币账单未知，不从token推造价格。

按匿名来源组做成对bootstrap（50来源组，5000次，统计抽样seed20261012，非LLM sampling seed）；每组先平均其问题/重复，再比较，点估计及区间因此不是上表按题加权的百分点差。仅描述开发gold字面差，不作为真实用户/留出泛化或语义显著性结论：

| 比较 | 来源均衡差值 | 95%区间 |
| --- | --- | --- |
| B2_dense minus B0_recent | -2.000 pp | [-9.000, 5.333] pp |
| B2_rrf minus B0_recent | -3.667 pp | [-11.667, 4.333] pp |
| B2_rrf minus B2_dense | -1.667 pp | [-4.667, 1.000] pp |

## 视图访问：真实空状态开销

独立安静阶段执行的run `20261012T073645Z-a5541902`，墙钟14.903 s。只读一致性备份生产账号库，在副本执行12槽位×3条件×50次×3轮=5400次；计数{'sessions': 50, 'messages': 206, 'memories': 0, 'claims': 0}，批准修订0，错误0，FK错误0，integrity=ok。未生成事实/来源/标签，实际model调用0、生产写入0，旧历史未导入。

| 条件 | 访问次数 | 访问p50/p95（ms） |
| --- | --- | --- |
| uncached | 1800 | 0.071/0.097 |
| cached | 1800 | 0.106/0.124 |
| invalidated | 1800 | 0.125/0.22 |

| 维护项 | 次数 | p50/p95（ms） |
| --- | --- | --- |
| cached_compile_ms | 36 | 2.558/3.599 |
| compile_and_invalidate_ms | 1800 | 5.003/6.367 |

每轮轮换条件顺序，访问延迟包含数据库事务、视图校验与结果装配；编译/强制失效成本另计。全部payload与同一实际状态参照一致；0已批准Claim意味着全部应澄清，显式空缓存命中不算完整view。缓存空结果有额外开销，未测有完整画像/真实更新时的正向收益、规模SLO、reader节省或B5/B6语义效用。副本close/checkpoint后才计算SHA；两个先前开发/有并行负载的5400次诊断单独保留，不混入这个安静阶段表。

## 保留的边界与下一步

自动来源资格核验：1个公开诊断包/206原话/0获准连续真实人物，旧归档未读。真实长期保持、真实变化恢复和越来越熟悉的效果均null/未测；根因是连续人物/日期资料不足，不能用匿名来源组或消息间距代替。

人工支持度/语义QA/独立真人gold/成对陪伴偏好按用户要求暂缓；生产明确批准权限没有改成自动批准。后续有真实新资料才开展Claim写入与已审核状态的B5/B6质量/性能对照；生产普通记忆RRF补检已接线，一般事件回查与实际质量仍另验收。当前来源召回已足，下一项自动诊断应定位回答/引用错误，保留旧gold/预测；修改协议时建立新版本，不根据本轮失败回改既有成绩。

完整工程验证371 passed / 84.82 s。工程临时输入覆盖未来缓存门控、融合去重/原文、坏分块/向量拒绝、无reader纯检索、注册分母/基线拒绝、实际副本隔离与checkpoint SHA；不作为用户实验。此前两个dirty预检（64题Dense/RRF各一次）不计注册重复，未据预检调参数/改gold。

实现：[retrieval.py](../../memoria/experiments/retrieval.py)、[access_eval.py](../../memoria/experiments/access_eval.py)、[编排器](../../scripts/evaluate_retrieval.py)；进度与验收见[研究计划](../plans/长期陪伴记忆研究与实验计划书.md)。

## 人工未知的派生评分修订

本轮汇总语义质量均为null。旧scorer曾把执行失败保守记为人工False/下界；该字段不作为真人评分。现修正policy v2：无judgments、无有效预测均保持人工未知，执行失败仍进入自动字面失败分母。9轮已有QA另存派生score，核验全部576项人工未知、0额外模型调用；原始预测/manifest/旧score的SHA保持，字面/证据数值不变。回执在账号reports/human-score-null-revisions-20261012.json，scorer代码/文件SHA与差异单独记录。受影响研究回归55 passed / 28.69 s；固定模型运行代码仍是040427c，不把后续metadata修正当新模型试验。

三组累计15个执行失败：13次未呈现引用、2次供应商调用失败；已知usage574/576，prompt/completion/total token为338376/15126/353502。QA逐轮墙钟4673.077 s，纯召回19.910 s；供应商负载/前缀缓存及网络未控制，整题/TTFT差异不能归因于检索算法。匿名来源组独立性未核验，bootstrap只描述本批开发数据，不作真实用户显著性或长期结论。
