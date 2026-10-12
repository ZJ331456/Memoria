# Weather：在线天气文本

[技能目录](../README.md) · [运行时指令](SKILL.md) · [HTTP](../../memoria/tools/http_get.py)

由 http_get 请求文本，不执行 shell、不自带离线天气。触发词含天气/气温/下雨、weather/forecast。

按用户城市优先 wttr.in 紧凑结果；必要时 Open-Meteo 需要可信经纬度，当前无地理编码工具。地点或网络不足时明确失败，不能生成天气当已查询。

模板白名单 wttr.in/api.open-meteo.com/open-meteo.com；实际可用性依赖网络/管理员配置。只校验初始主机，不是完整重定向隔离；无 Key 不等于离线，不自动保存地点为个人事实。

## 验证

```powershell
# 仓库根目录
.\.venv\Scripts\python.exe -m pytest -q tests/test_skills.py -k 'builtin_skills_directory_present or http_host_allowlist'
```

仅目录与主机匹配检查，不访问天气服务或报告效果分数。
