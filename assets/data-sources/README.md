# 公开来源与Benchmark清单

[项目首页](../../README.md) · [当前评测](../evaluation/README.md) · [导入说明](../../docs/implemented/真实对话数据与实验入口.md)

| 清单 | 来源与使用范围 |
| --- | --- |
| [lccc-base.manifest.json](lccc-base.manifest.json) | 固定LCCC版本、选择规则和SHA；50段/206原话诊断子集，真实人物、日期和跨会话连续身份未知 |
| [locomo10.manifest.json](locomo10.manifest.json) | 官方LoCoMo-10，10对话/5,882原话/1,986题；构建研究benchmark，文本模式，不读图片 |

原始LCCC归档和子集位于忽略的data/datasets/lccc-base；账号保存自己的原文副本和来源映射。LoCoMo原始输入位于data/benchmarks/locomo，冻结inputs/history/gold与逐题运行留在账号研究目录。Git仅存来源、聚合与指纹。

```powershell
.\.venv\Scripts\python.exe scripts/prepare_real_dialogues.py --limit 50 --user your_username
```

目标账号须已建立；归入其研究空间不代表原话来自账号本人，不自动生成个人事实。公开下载不等于已核验商业训练、展示或再分发权益；LCCC清单记录的是构建脚本许可声明，LoCoMo清单记录CC-BY-NC-4.0。商业用途仍需按[计划](../../docs/plans/长期陪伴记忆研究与实验计划书.md#18-下一阶段功能优先级与商用准备)独立验收。
