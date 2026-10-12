# Memory Review：普通个人记忆审查

[技能目录](../README.md) · [指令](SKILL.md) · [记忆引擎](../../memoria/memory/README.md) · [Memory Studio](../../studio/README.md)

recall_memory 抽查过时/冲突/琐碎普通记忆，必要时同义检索。先预览保留/更新/删除及理由，明确确认后才按 ToolPolicy 调用 memorize/forget_memory。

memorize 可创建/强化/替代普通记忆，forget_memory 删除该表；技能预览不是审核系统，不能将写权限用于 Claim 或共享提案。普通纠正走 /api/memories 对应接口以保留原因/版本；结构化 Claim 走 Studio 来源/修订/明确审核；共享走 /api/shared/* 独立治理。

Studio 无旧“检查并纠正”或“共享治理”聊天页面，不按历史界面名称操作。来源/作者不足保留未知，不从公开语料生成账号本人偏好。

## 验证

```powershell
# 仓库根目录
.\.venv\Scripts\python.exe -m pytest -q tests/test_skills.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_core.py -k 'user_correction or automatic_supersede'
```

工程目录/版本验证，不证明真实冲突识别效果。
