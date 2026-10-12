# Skill Creator：编写 Memoria 技能

[目录与格式](../README.md) · [运行时指令](SKILL.md) · [加载器](../../memoria/skills/README.md)

指导创建/改写项目 SKILL.md，触发词含创建技能/写技能、skill/SKILL.md。当前运行时无 shell/write_file，不自行写文件或安装插件。

```text
skills/<name>/
├── README.md       开发者用途、入口、限制和验证
└── SKILL.md        一级目录读取的运行时指令
```

先生成简洁正文/单行 metadata，由开发者保存重载并查 /api/skills。references、README 和脚本不自动递归读入；仅引用实际工具，不假设插件 API、复杂 YAML 或技能专属权限。

正式接口需用户登录，服务 Token 不替代登录。revision 为完整 SKILL.md SHA-256，加载/创建不等于任务质量验收。

## 验证

```powershell
# 仓库根目录
.\.venv\Scripts\python.exe -m pytest -q tests/test_skills.py tests/test_layer_settings_config.py
```

测试目录/格式/指纹，新业务仍需来源及效果验证。
