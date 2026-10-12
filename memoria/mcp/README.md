# MCP：每账号 stdio 工具接入

[核心包](../README.md) · [工具](../tools/README.md) · [部署与飞书授权](../../docs/implemented/MCP部署与授权.md)

## 文件与生命周期

| 文件 | 职责 |
| --- | --- |
| [config.py](config.py) | JSON server 配置、字段校验、环境变量占位符 |
| [client.py](client.py) | stdio 子进程、JSON-RPC initialize、工具分页/调用、ping 与超时清理 |
| [host.py](host.py) | 连接/重载/停止，按 mcp_{server}_{tool} 注册和注销 |
| [__init__.py](__init__.py) | 导出接口 |

正式网关读取 `data/users/<UUID>/config/mcp_servers.json`，新账号无默认 MCP；内部 Settings 配置不是正式多用户入口。命令/cwd/env/工具名单/risk/timeout 由本机管理员维护，状态只公开环境变量名。

生命周期随账号运行时启停；GET /api/mcp、POST /api/mcp/reload 需要登录。仅登记发现且获允许的工具，重载注销旧项。server risk、工具参数/输出/超时及本轮授权共同限制执行，普通记忆写授权不覆盖外部写操作。

## 本机入口

仓库根目录操作，依赖安装到 `.mcp`，不改全局 npm/conda base：

```powershell
.\.venv\Scripts\python.exe scripts/install_mcp.py
# USERNAME 替换为已有账号
.\.venv\Scripts\python.exe scripts/configure_mcp.py --user USERNAME
.\.venv\Scripts\python.exe scripts/check_mcp.py --user USERNAME
```

搜索/GitHub/飞书的固定版本、状态和 OAuth 见部署文档。飞书用 [configure_feishu.py](../../scripts/configure_feishu.py) 私密输入/浏览器授权；安装、握手、授权和真实调用分别验收。

## 边界与验证

仅支持 MCP tools over stdio，未实现通用 resources/prompts/HTTP transport 或多节点沙箱。账号配置隔离不等于子进程系统权限隔离。

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_mcp_lifecycle.py tests/test_round10_tool_search_mcp.py tests/test_user_privacy.py
```

临时服务器/传输验证生命周期和权限，真实服务另用只读检查。
