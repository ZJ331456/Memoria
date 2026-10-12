# Summarize：真实材料摘要

[技能目录](../README.md) · [运行时指令](SKILL.md) · [工具](../../memoria/tools/README.md)

用于文本、历史对话、普通记忆和已获取网页。触发词含总结/摘要/链接、summarize/transcript；视频关键词不代表支持下载或转写。

1. search_history/recall_memory 查实际材料，网页用 http_get。
2. 给结论、重点、待办和未知，明确未获取部分。
3. 保存先给候选，明确授权后调用普通 memorize。

默认主机白名单只有天气服务，其他网页需管理员配置；重定向目标未完整隔离。截断不代表全文，动态/付费/不可达内容不得补写。技能不创建或批准 Claim，不改共享空间。

## 验证

```powershell
# 仓库根目录
.\.venv\Scripts\python.exe -m pytest -q tests/test_skills.py
```

测试目录/加载，真实摘要质量和网页可达性另测。
