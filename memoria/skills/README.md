# Skills：发现与正文加载

[核心包](../README.md) · [项目技能目录](../../skills/README.md) · [工具](../tools/README.md) · [运行时](../runtime/README.md)

## 文件与入口

[catalog.py](catalog.py) 实现 SkillCatalog 扫描、front matter、依赖、触发词评分和注入；[__init__.py](__init__.py) 导出接口；[builtin.py](../tools/builtin.py) 注册 load_skill。

配置 `[agent.skills]` 的 enabled/directory/max_inject，默认项目 skills/。只扫描一级子目录 SKILL.md，README、任意 Markdown/脚本/references 不自动加载。

解析简单逐行 name/description/triggers/always 和单行 JSON metadata 依赖，不是完整 YAML。缺依赖可列出但不自动匹配。select 合并 always、显式选择和触发词评分，非 always 最多 max_inject，自动注入每份正文默认 4000 字符。

GET /api/skills、GET /api/skills/{name}、POST /api/skills/reload 查询/读取/重扫，正式网关需登录。load_skill 只返回正文，不执行脚本或增加工具；完整加载仍受上下文/读取配额。

## 程序记忆边界

revision 是完整 SKILL.md SHA-256，含元数据，API/trace 可定位版本。匹配/注入/加载成功不等于任务成功；Git 管理历史，当前不根据情景自动发布技能。

技能是低信任指令，不是权限主体或沙箱；写权限仍由 ToolPolicy/注册表决定，共享用独立 Agent key/ACL。五项内置技能见项目目录。

## 验证

在仓库根目录：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_skills.py tests/test_layer_settings_config.py tests/test_round11_drift.py
```

测试验证发现、指纹与配额，不评估真实任务效果。
