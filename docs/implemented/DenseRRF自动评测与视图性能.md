# 已实现：Dense/RRF自动评测与视图访问基准

更新：2026-10-12。用户要求开始自动评测，人工语义评分、独立真人审核、主观陪伴偏好评估暂缓。当前数据仍是50段匿名公开对话，不拼接为连续人物，不变成qin画像；真实跨会话长期效果不可测。

## 源前缀召回与真实reader

`research.py run --mode retrieval`只检索、不调用LLM；独立`score`才读取冻结gold。`--mode qa`以相同来源/问题/预算调用真实reader。新增`B2_dense`和`B2_rrf`：

- BGE中文small v1.5，原始原话完整tokenizer分块、父消息最佳块余弦；查询使用生产中文query instruction，无根据gold调出的阈值。
- RRF融合Dense top8和现有compact FTS/BM25 top8，k=60、父消息ID去重，稳定原顺序打破同分。最终至多8条完整原话，12000序列化字符预算。
- 文档向量只在当前运行缓存，namespace/消息ID/原文SHA共同标识；候选集合始终是该题可见前缀，其他题已编码的未来消息不可成为候选。记录实际块span、Dense分/分支rank/融合分、缺项和预算遗漏。
- `--embedding-device auto`显式直读项目已有本地权重，仅改变本次运行。当前生产指向本机HTTP Embedding，不修改生产配置或base环境。实际预检使用CUDA、512维。

这是**来源读取评测接线**，不是生产`/api/memory-access`已接入Dense补检。生产Claim状态与匿名来源仍分开；不会拿未审核片段填完整个人状态。

```powershell
.\.venv\Scripts\python.exe scripts/research.py --user qin run --mode retrieval --protocol lccc50-codex-dev-qa-20261012-v2 --baseline B2_rrf --embedding-device auto
.\.venv\Scripts\python.exe scripts/research.py --user qin score --run <实际run_id>
.\.venv\Scripts\python.exe scripts/evaluate_retrieval.py --user qin --allow-remote
```

固定注册文件：[自动评测协议](../../configs/research-retrieval-eval-20261012.json)。12轮纯检索（窗口/compact/Dense/RRF各三次，768题次）；9轮真实QA（窗口/Dense/RRF各三次，计划576次reader调用）。同一冻结64题、AI gold来源不改、main deepseek-flash、1024输出token/温度0/top_p1/无重试/并发1。顺序在调用前固定；QA同时重跑窗口，避免只与旧日期/代码的结果比较。

编排器要求clean已提交代码和输入/gold/历史SHA、BGE指纹、实际reader别名一致，每轮核对代码不变；所有结果/失败/原始调用在账号独立run_id下。错误不缩小分母。权重服务端revision未知，字面匹配不是语义正确率，也不代表本地约1B模型表现。

初次dirty开发预检Dense为64题成功/0调用，41道可知题全部证据召回，22.745780秒含本地模型加载；另有独立RRF预检。预检不计入固定协议重复次数，未修改gold或根据分数调整参数。正式协议结果随后登记，不能把预检当长期效果。

## 实际账号视图访问基准

```powershell
.\.venv\Scripts\python.exe scripts/research.py --user qin benchmark-access --repeats 3 --iterations 50
```

只读备份本账号生产SQLite，在独立实验副本初始化Claim表；每个12槽位三种受控路径：清缓存直接查、显式编译后缓存读、编译后推进对应槽位generation再读。三轮轮换顺序、每组每槽位50次，共5400次访问。分别记录访问墙钟与编译/失效维护成本、payload等价、路径、完整性和全部失败。

不生成任何Claim、原话或标签。当前0条已批准Claim，只能测**真实空状态查找与显式空视图的额外开销**；即使有效空缓存被读取，path仍clarify，不当成完整view命中。未比较完整画像的正向缓存收益，不给0条数据外推规模SLO。副本关闭/SQLite checkpoint后再计算产物SHA；生产配置/原话/记忆/旧归档不写。

## 长期效果及人工项

自动检查已登记来源/同意、稳定人物、原始时间及跨会话条件；公开匿名来源不能通过人物连续性验收。当前真实连续人物为0，长期保持、真实变化恢复和越用越熟悉的指标为null/未测。无需人工介入的来源召回、字面QA、引用合法性、阶段墙钟、usage和空状态访问基准继续运行。人工支持度/语义QA/偏好、正式gold审核暂缓；生产Claim明确批准的权限不改为自动批准。

工程验证：全量371 passed / 84.82 s；其中临时输入验证未来缓存不泄漏、RRF原ID/去重、namespace/SHA绑定、坏分块/维度/非有限数值拒绝、检索模式无reader调用、生产副本隔离与checkpoint后的SHA。工程输入不成为研究数据。

剩余内容见[研究计划](../plans/长期陪伴记忆研究与实验计划书.md)；原有六轮数据见[此前QA](真实QA对照评测结果.md)。
