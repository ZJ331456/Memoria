# MCP 本地部署与飞书授权

更新：2026-10-11。目录网站用于发现候选，实际部署依据上游仓库、固定版本和真实调用。三个服务接入 Memoria-Agent 的 stdio MCP 主机；与 Codex 桌面端插件设置独立。

## 已安装服务与验证状态

| 服务 | 上游与固定版本 | 当前状态 | 项目允许的工具 |
| --- | --- | --- | --- |
| 网页搜索 | [Open-WebSearch](https://github.com/Aas-ee/open-webSearch)，`open-websearch@2.2.0` | 真实初始化、百度搜索返回链接成功 | search、fetchWebContent |
| GitHub | [GitHub 官方 MCP](https://github.com/github/github-mcp-server)，[v2.0.2](https://github.com/github/github-mcp-server/releases/tag/v2.0.2) | get_me 确认 ZJ331456；读取本仓库 README 成功 | 身份、仓库/代码检索、文件、分支、提交、issue/PR 读取，共 8 个工具 |
| 飞书 | [飞书官方 MCP](https://github.com/larksuite/lark-openapi-mcp)，`@larksuiteoapi/lark-mcp@0.5.1` | 已安装，OAuth 命令已配置；用户授权尚未完成，默认禁用 | 文档正文、云盘文件列表、知识库空间列表/节点读取，共 4 个工具 |

搜索无需付费 API key，但依赖搜索站点可访问性及限流。当前网络实测百度返回结果；Bing 相关性不足，早期 DuckDuckGo 方案未取得有效结果，故没有作为默认服务。返回网页链接只证明接线可用，内容仍需核对原始来源。

GitHub 使用 `--read-only` 并限制 toolsets；launcher 从 Git Credential Manager 读取现有认证，仅通过子进程环境传递 token。可用私密环境变量 `GITHUB_PERSONAL_ACCESS_TOKEN` 覆盖，验证脚本仍要求账号为 ZJ331456。API 配额及仓库权限受账号限制。Git 提交/推送由 CLI 按[提交规范](github提交规范.md)执行。

## 本地安装与运行

在仓库根目录、已有项目 `.venv` 中执行；要求 Windows x86_64、Node.js 20+、Git 和可用网络。

```powershell
.\.venv\Scripts\python.exe scripts/install_mcp.py
.\.venv\Scripts\python.exe scripts/configure_mcp.py
.\.venv\Scripts\python.exe scripts/check_mcp.py
```

- npm 依赖仅安装在 `.mcp/node`，版本与完整依赖锁在 `deploy/mcp/`；GitHub exe 位于 `.mcp/github`，下载按官方 release SHA256 校验。
- 安装清单 `.mcp/installation.json`，实际调用记录 `data/reports/mcp-check.json`；这些本机文件不提交 Git。
- `configure_mcp.py` 生成带绝对 Python/脚本路径及 cwd 的 `data/mcp_servers.json`，保留已配置开关和自定义服务。模板 `mcp_servers.example.json` 的相对命令仅适用于从仓库根目录启动。
- 服务随 Memoria-Agent 启动，不另外启动公网监听。修改配置后通过 `POST /api/mcp/reload` 重载，`GET /api/mcp` 查看连接状态；启用 API 认证时携带已配置的管理员认证。
- MCP 工具受项目工具注册、风险策略及按需工具搜索限制；工具发现列表可多于实际开放的白名单工具。

## 飞书：用户稍后私密输入并授权

已有飞书自建应用，在开放平台配置 OAuth 重定向地址：**`http://localhost:3000/callback`**。发布应用并确保当前用户在可用范围内；开通下列只读能力。实际可访问的文档/知识库还取决于用户自身权限。

本项目请求的 OAuth scope：`offline_access docx:document:readonly drive:drive:readonly wiki:wiki:readonly`。与 [官方 CLI](https://github.com/larksuite/lark-openapi-mcp/blob/main/docs/reference/cli/cli.md) 一致使用用户 token 模式。

```powershell
# 在本机终端输入；App Secret 不回显，随后完成浏览器授权
.\.venv\Scripts\python.exe scripts/configure_feishu.py

# 授权后验证真实飞书 API，再重载项目 MCP
.\.venv\Scripts\python.exe scripts/check_mcp.py --server feishu
```

已有私密 JSON 文件时也可 `--credentials-file C:\私密目录\feishu.json`（字段 app_id、app_secret）；之后重新登录用 `--login-only`。不要把内容粘贴到聊天或版本库。

脚本把应用凭据保存到被忽略的 `.mcp/feishu.credentials.json`，仅传给官方进程环境。OAuth token 由官方工具管理，不写入服务器配置、报告或命令参数。官方登录命令成功后脚本启用 feishu；实际业务调用是否成功仍由 check_mcp 单独确认。如果回调超时，检查应用发布、权限、回调地址及 3000 端口后重新登录。

只读工具名按上游 schema：`docx_v1_document_rawContent`、`drive_v1_file_list`、`wiki_v2_space_list`、`wiki_v2_space_getNode`。本次没有浏览器授权，也没有记录飞书业务调用成功。

## 主机优化与工程验证

主机最多并行初始化 4 个服务，支持 tools/list 分页、服务端 ping 和 JSON-RPC 请求区分；工具名超长或字符清洗时附加摘要以降低冲突。严格校验开关和超时；超时/取消关闭连接，失效工具移出注册表。Windows 下隐藏服务窗口，并在停止时清理 launcher 所属进程树。

启动日志不打印凭据命令或上游 stderr 正文，检查报告仅保留身份、状态及公开搜索链接。工程验证见 `tests/test_mcp_lifecycle.py`；真实联网验证见 `scripts/check_mcp.py`，两种验证结果分别记录。2026-10-11 本机 Node.js 22.16.0；项目 Host 实际注册搜索 2 个/GitHub 8 个工具，ToolRegistry 中搜索与 get_me 调用成功，停止后注册表清空、没有发现遗留服务进程。飞书仍待用户授权。

## 其他免费候选：仅推荐，未部署

以下项目开源、可本地运行，基础使用无需购买 MCP 服务/API key；本地计算、目标站点账号和网络成本仍由使用者承担。

| 候选 | 能力与建议 |
| --- | --- |
| [Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp) | 浏览器自动化，适合必须交互才能访问的网页；比普通搜索更耗资源，按需再接入 |
| [官方 Filesystem MCP](https://github.com/modelcontextprotocol/servers/blob/main/src/filesystem/README.md) | 文件读取/搜索，适合限定资料目录；接入时用明确目录和只读工具白名单，当前客户端不宣告 roots 支持 |
| [官方 Fetch MCP](https://github.com/modelcontextprotocol/servers/blob/main/src/fetch/README.md) | 将网页内容转为模型易读文本，与现有 fetchWebContent 有重叠；只有需要其格式化能力再增加 |

没有默认增加第二套“记忆 MCP”：当前任务应验证本项目的来源、纠正与长期状态管理，额外记忆库会造成事实归属与评测边界混淆。

候选发现入口：[mcp.so 中文目录](https://mcp.so/zh)、[mcp-cn](https://mcp-cn.com/)。目录收录不等于已安装、免费无限制或业务验证成功。
