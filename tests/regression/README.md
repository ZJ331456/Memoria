# 工程回归与自定义夹具

[测试导航](../README.md) · [公开评测](../../eval/README.md)

这些文件从 `eval/` 移入本目录，用于检测实现是否退化。问题、规则与预期值由项目维护者编写，不能作为公共 benchmark 成绩。旧的三份合成分数报告已从评测目录移除，不再作为项目能力数字引用。

| 入口 | 用途 |
| --- | --- |
| [run_governance.py](run_governance.py) / [governance_cases.json](governance_cases.json) | 二十四项治理机制回归；授权、撤回、版本、来源 |
| [governance_eval.py](governance_eval.py) / [test_governance_eval.py](test_governance_eval.py) | 夹具预测的独立评分及评分器测试 |
| [run_memory_layers.py](run_memory_layers.py) | 十五项分层机制与边界回归，包含固定时钟、容量和技能版本 |
| [run_seeded.py](run_seeded.py) / [seeded_memory_cases.json](seeded_memory_cases.json) | 自定义中文检索与门控回归 |
| [memory_eval.py](memory_eval.py) / [memory_cases.json](memory_cases.json) | 检索排名评分器的少量夹具 |
| [benchmark_shared_memory.py](benchmark_shared_memory.py) | 可选的合成存储微基准；不是公共数据集评测 |
| [run_companion.py](run_companion.py) / [companion_inputs.json](companion_inputs.json) | 四种中文变化轨迹、八个检查点；区分人工整理和生产自动抽取，gold 独立评分 |

```bash
python -m pytest -q tests/regression tests/test_memory_layers_eval.py
python -m tests.regression.run_governance --min-pass-rate 1 --max-leak-rate 0
python -m tests.regression.run_memory_layers --min-pass-rate 1
python -m tests.regression.run_seeded --min-recall 0.75
```

本次仅迁移与保留回归测试，没有重新运行 1000 条合成性能负载。公开集下载、固定五题选择、实际模型评测与报告归档均在 `eval/`。历史命令 `python -m eval.run_governance` 等已更换为上方的新入口。

## 中文长期陪伴诊断（2026-10-11 新增）

```powershell
.\.venv\Scripts\python.exe -m tests.regression.run_companion --vector-backend json
.\.venv\Scripts\python.exe scripts/check_companion_local.py --device cuda
```

第一条不调用模型，只核验夹具指定的写入和来源；第二条启动隐藏的临时本机服务，用真实 BGE/Qwen 核验长文索引，再运行人工整理与生产抽取两种条件，结束后关闭自己的服务。模型必须已下载在项目 `model/`，依赖使用独立 `.venv`。`run_companion` 默认不执行 LLM/embedding；已有服务可加 `--embedding --reader --mode extracted --config experiment.toml`。远程模型须明确 `--allow-remote`，并在角色配置声明供应商实际/未知的思考策略。

每次 run 输出独立 manifest、cases/events/calls/prompts JSONL 和 metrics，保留失败与完整分母。schema、参数、阶段耗时与限制见[实验说明](../../docs/实验记录与长记忆分块索引.md)，核验汇总在 [results/companion_local_20261011.json](results/companion_local_20261011.json)。这四个虚构轨迹不是独立人工核验研究数据；来源/回答关键词检查不等于语义 QA，CLI 退出成功只表示未发生运行错误，低分仍会记录。
