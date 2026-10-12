# Evidence：需求、预算与选择轨迹

[Memory](../README.md) · [实现与诊断](../../../docs/implemented/条件时序抽取与预算证据选择.md)

| 文件 | 职责 |
| --- | --- |
| [requirements.py](requirements.py) | 仅从query/parser构建有限机械需求，候选特征覆盖；不接受gold |
| [selector.py](selector.py) | Top-K/Coverage/词面MMR/成本归一覆盖贪心；整组、不截原话、来源去重 |
| [budget.py](budget.py) | 完整证据JSON的chars/utf8_bytes/真实reader_tokens成本契约 |
| [reader_tokenizer.py](reader_tokenizer.py) | 可信local:// reader的离线tokenizer；HTTP/远程不可验证时拒绝 |
| [trace.py](trace.py) | 每条候选决策、排名值、边际成本、新覆盖和冗余 |

统一访问一次生成候选，在同池同预算比较四种策略；诊断不自动批准或调用reader。Claim同槽位条件/否定保持原子组，source去重。预算不涵盖system/query/tools/output；字节不是远程Token实测，BGE tokenizer不得冒充reader。

MMR使用中文bigram/英文词Jaccard而非Dense向量；Coverage是查询预测的机械特征，`semantic_sufficiency_verified=false`。α=1/β=1/γ=0.35为未调优基线，MMR lambda=0.5；真实50公开短对话的2048字符诊断没有优于Top-K，默认coverage暂不切换。详细口径/失败/未知见实现文档。

仓库根目录验证：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_algorithm_pipeline.py tests/test_memory_studio.py tests/test_research_workflow.py
.\.venv\Scripts\python.exe scripts/research.py --user qin select-evidence --help
```

临时测试不是用户数据；replay是私有真实来源研究，不读取旧个人归档或修改生产记忆。
