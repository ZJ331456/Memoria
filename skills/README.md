# 内置技能说明

[项目首页](../README.md) · [加载器](../memoria/skills/README.md) · [工具](../memoria/tools/README.md)

个人运行时只扫描一级子目录 SKILL.md。本页和各 README 面向开发者，不注入模型。技能不自动执行脚本、注册工具或授予权限。

## 导航

| 技能 | 用途与工具 |
| --- | --- |
| [summarize](summarize/README.md) | 实际材料摘要；search_history/recall_memory/http_get |
| [weather](weather/README.md) | 在线地点天气文本；http_get/白名单 |
| [skill-creator](skill-creator/README.md) | 指导生成 SKILL.md；运行时无 shell/write_file |
| [memory-review](memory-review/README.md) | 普通记忆冲突/过时预览，确认后按策略写入 |
| [drift-digest](drift-digest/README.md) | 默认关闭 Drift 的小范围抽查；禁止 forget_memory |

均面向个人工具，不贯穿共享 Agent 身份，也不替代 Claim 来源/预期修订号/明确批准。加载成功不等于任务成功。

## 格式与加载

配置 `[agent.skills]` enabled=true、directory="skills"、max_inject=2；保存后重启或登录 POST /api/skills/reload。GET /api/skills 发现/可用性，GET /api/skills/{name} 正文，正式网关需用户登录。

```markdown
---
name: skill-name
description: 用途与触发场景
triggers: 触发词1, 触发词2, skill-name
always: false
metadata: {"memoria":{"requires":{"bins":[],"env":[]}}}
---

# 技能名称

只描述已注册工具、真实来源、失败处理与权限边界。
```

front matter 是轻量逐行解析，metadata 单行 JSON，不支持完整 YAML 嵌套。缺命令/环境依赖标不可用，不自动安装；无 triggers 时从 description 取词面关键词。always 与普通匹配分别计数，自动单份正文默认 4000 字符；完整 load_skill 仍受本轮/上下文预算。

## 添加和验证

新增 skills/<name>/SKILL.md 与 README，声明准确工具/依赖；由开发环境落盘，个人运行时不写文件。保留/删除普通记忆仍须真实来源和权限。

```powershell
# 仓库根目录
.\.venv\Scripts\python.exe -m pytest -q tests/test_skills.py tests/test_layer_settings_config.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_round11_drift.py -k drift_digest_skill_present
```

工程测试验证发现/加载/版本，不证明真实摘要、天气或整理质量。
