# MCP 本地部署与飞书授权

更新：2026-10-12。目录网站用于发现候选，实际部署依据上游仓库、固定版本和真实调用。三个服务接入 Memoria-Agent 的 stdio MCP 主机；与 Codex 桌面端插件设置独立。

手机给应用机器人发消息属于另一条入口，不能由MCP OAuth代替；已新增[飞书机器人对话接入](飞书机器人对话接入.md)，需官方长连接事件订阅、发消息权限和Memoria账号绑定。下文OAuth回调只负责工具授权。

## 已安装服务与验证状态

| 服务 | 上游与固定版本 | 当前状态 | 项目允许的工具 |
| --- | --- | --- | --- |
| 网页搜索 | [Open-WebSearch](https://github.com/Aas-ee/open-webSearch)，`open-websearch@2.2.0` | 真实初始化、百度搜索返回链接成功 | search、fetchWebContent |
| GitHub | [GitHub 官方 MCP](https://github.com/github/github-mcp-server)，[v2.0.2](https://github.com/github/github-mcp-server/releases/tag/v2.0.2) | get_me 确认 ZJ331456；读取本仓库 README 成功 | 身份、仓库/代码检索、文件、分支、提交、issue/PR 读取，共 8 个工具 |
| 飞书 | [飞书官方 MCP](https://github.com/larksuite/lark-openapi-mcp)，`@larksuiteoapi/lark-mcp@0.5.1` | qin 已完成 OAuth；知识空间列表与云盘列表真实调用成功，仅 qin 启用 | 文档正文、云盘文件列表、知识库空间列表/节点读取，共 4 个工具 |

搜索无需付费 API key，但依赖搜索站点可访问性及限流。当前网络实测百度返回结果；Bing 相关性不足，早期 DuckDuckGo 方案未取得有效结果，故没有作为默认服务。返回网页链接只证明接线可用，内容仍需核对原始来源。

GitHub 使用 `--read-only` 并限制 toolsets；launcher 从 Git Credential Manager 读取现有认证，仅通过子进程环境传递 token。可用私密环境变量 `GITHUB_PERSONAL_ACCESS_TOKEN` 覆盖，验证脚本仍要求账号为 ZJ331456。API 配额及仓库权限受账号限制。Git 提交/推送由 CLI 按[提交规范](github提交规范.md)执行。

## 本地安装与运行

在仓库根目录、已有项目 `.venv` 中执行；要求 Windows x86_64、Node.js 20+、Git 和可用网络。

```powershell
.\.venv\Scripts\python.exe scripts/install_mcp.py --help
.\.venv\Scripts\python.exe scripts/install_mcp.py
.\.venv\Scripts\python.exe scripts/configure_mcp.py --user qin
.\.venv\Scripts\python.exe scripts/check_mcp.py --user qin
```

安装脚本无参数时才执行部署；2026-10-12补齐参数解析，`--help`仅显示帮助，未知参数退出且不安装。已核对两种参数路径及本地运行目录未被它们修改；这不是一次新的MCP业务授权或联网调用验证。

- npm 依赖仅安装在 `.mcp/node`，版本与完整依赖锁在 `deploy/mcp/`；GitHub exe 位于 `.mcp/github`，下载按官方 release SHA256 校验。
- 安装清单 `.mcp/installation.json`，实际调用记录 `data/users/<UUID>/reports/mcp-check.json`；这些本机文件不提交 Git。
- `config/mcp.toml`集中管理服务定义、命令/cwd/env、工具名单和各UUID账号启用列表；`configure_mcp.py --user USERNAME`只更新该文件本人启用项，已有项保留。`{project}`和`{account_dir}`在服务器展开，飞书OAuth路径始终指向本人。公开模板为[config/mcp.example.toml](../../config/mcp.example.toml)，旧根JSON模板和账号JSON已退休。
- 用户首次访问业务 API 时创建该账号运行时并启动 MCP，不另外启动公网监听。修改配置后通过 `POST /api/mcp/reload` 重载，`GET /api/mcp` 查看该账号连接状态；使用该用户的登录 Bearer 或 Cookie。
- MCP 工具受项目工具注册、风险策略及按需工具搜索限制；工具发现列表可多于实际开放的白名单工具。

## 飞书详细教程：找到 App Secret 并完成本机授权

核对日期：2026-10-11。以下是本项目已安装的官方 MCP 0.5.1 的授权流程；后台菜单名称可能调整，以页面实际显示为准。本节提供操作步骤，当前 qin 已完成本机授权和两项只读业务检查。

### 1. 先确认你已有的是哪一种“飞书应用”

**App Secret 在飞书开放平台的应用后台查看，本地飞书客户端的设置页不提供该应用凭据。** 官方 SDK 将获取位置说明为开发者后台 → 凭证与基础信息 → 应用凭证。[飞书官方 SDK 说明](https://github.com/larksuite/oapi-sdk-nodejs/blob/main/doc/ISV.APP.README.zh.md)

| 你目前已有的东西 | 是否能直接用于本项目 |
| --- | --- |
| 电脑上安装了飞书、可以聊天和打开文档 | 还需要开放平台自建应用，安装客户端本身不会给你 App Secret |
| 开放平台中自己创建或有管理权限的企业自建应用 | 可以，在应用后台获取 App ID / App Secret |
| 群设置里添加的自定义机器人、一个 Webhook 地址 | Webhook 地址及其签名密钥不能填写到本项目的 App Secret；仍需开放平台应用 |
| 同事创建的开放平台应用 | 需要该应用管理权限；由应用所有者添加协作者或提供私密配置 |

本项目通过 API 读取文档、云盘和知识库；本轮不要求你额外创建群聊机器人，也不用把机器人拉入群。

### 2. 打开开发者后台并找到你的应用

1. 在本机浏览器打开 [飞书开发者后台](https://open.feishu.cn/app)。这是中国版飞书后台，本项目使用 `open.feishu.cn`。
2. 按页面提示登录，可以用飞书客户端扫码。登录后选择**创建应用时所属的企业/组织**；如果有多个身份，切换到正确组织。
3. 在应用列表中点击你已有的自建应用，进入应用详情。
4. 如果没有应用列表项，先确认账号/企业是否选对，以及是否有该应用的管理权限。
5. 若此前只有本地飞书客户端、实际没有自建应用：在后台选择“创建企业自建应用”，填写名称（例如 Memoria-MCP）、说明及页面要求的信息。企业限制创建时，需要管理员协助。已有应用则直接使用，不必重复创建。

创建和管理流程以 [飞书官方自建应用指南](https://open.feishu.cn/document/home/introduction-to-custom-app-development/self-built-application-development-process) 为准。

### 3. 查找 App ID 和 App Secret

1. 在应用详情左侧导航找到“凭证与基础信息”；部分布局把它放在“基础信息”分组内。
2. 在右侧“应用凭证”区域找到 **App ID**，本项目脚本要求它以 `cli_` 开头。
3. 同一区域找到 **App Secret**。被圆点遮住时，使用旁边的查看/眼睛或复制按钮；如果页面要求身份验证，先完成验证。
4. 暂时保留这个后台页面，稍后把两个值分别粘贴到**本机终端**的输入提示中。
5. 如果没有查看权限，联系应用所有者/管理员开通管理权限。不要用“重置/刷新 Secret”代替“查看/复制”，重置会使依赖旧凭据的调用失效。

几个常见名称不能混用：

| 名称 | 本项目如何使用 |
| --- | --- |
| App ID | 应用标识，终端第一个输入项 |
| App Secret | 应用密钥，终端第二个输入项，输入不回显 |
| 飞书登录密码 / 验证码 | 仅用于平台登录，不填到 App Secret |
| Verification Token / Encrypt Key / Webhook 签名密钥 | 用于其他事件/机器人场景，不填到 App Secret |
| user_access_token | 浏览器 OAuth 后由官方工具取得并管理，用户不需要手动复制 |

已存凭据位于该账号的 `data/users/<UUID>/secrets/feishu.credentials.dpapi`，UUID 由服务器生成。仅检查存在性，不打印文件内容；目录定位可参考本机 `data/users/owners.local.json`。旧全局明文凭据路径不再使用。

### 4. 配置 OAuth 回调地址

在同一个应用详情中，进入“开发配置 → 安全设置”（或直接显示的“安全设置”），找到“重定向 URL / OAuth 重定向 URL”，添加并保存：

```text
http://localhost:3000/callback
```

本项目官方 MCP 登录命令明确使用 `--host localhost --port 3000`。地址的协议、主机、端口、路径保持一致；不要改为项目 API 的 2237 端口，不要换成 `127.0.0.1`，也不要多加末尾 `/`。这是浏览器授权结束后的本机回调，不需要部署公网网站或购买域名。先启动授权命令再访问它；平时直接打开地址无法连接是正常情况。[官方 MCP 的回调约定](https://github.com/larksuite/lark-openapi-mcp/blob/main/README_ZH.md)，[飞书官方安全设置示例](https://www.feishu.cn/content/7317091221654224898)

### 5. 开通本项目请求的权限

进入应用的“开发配置 → 权限管理”（或“权限管理”），选择开通/添加权限；按下表中的 **scope 字符串**搜索。若后台区分应用身份和用户身份，确认允许**用户身份 user_access_token** 对应的权限。本项目不以租户身份替代个人授权。

| 当前请求 scope | 用途 |
| --- | --- |
| `docx:document:readonly` | 读取新版云文档内容 |
| `drive:drive:readonly` | 读取云盘信息与文件列表 |
| `wiki:wiki:readonly` | 读取知识库空间与节点 |
| `offline_access` | 请求离线访问/刷新授权，供官方工具管理后续 token |

这些字符串来自本项目 `scripts/mcp_server.py` 的 FEISHU_SCOPE，也是登录时请求的范围。`offline_access` 是 OAuth scope，不是某个业务 API 工具；若权限面板没有同名业务权限，检查该应用 OAuth/offline_access 的平台配置说明，不要通过开通“全部权限”绕过错误。平台提示某项不支持或未开通时，先核对用户身份与应用类型并处理具体缺项。

确认开通后按后台要求保存、申请权限审批和发布版本。开了 API scope 仍须完成用户 OAuth，且**不能扩大你本人对具体文档/知识库的权限**。本项目仅开放 4 个读取工具，没有发送消息、创建文档或编辑内容工具。

### 6. 发布版本并把自己加入可用范围

1. 进入“应用发布 → 版本管理与发布”（或“版本管理与发布”）。
2. 创建版本，填写后台要求的版本号、更新说明，并确认权限变更。
3. 在“可用范围”中至少加入你将用于浏览器授权的飞书账号；先限定自己/实际需要的成员即可。
4. 保存并申请发布，等待企业管理员审核；以后台的已生效/已发布状态为准。
5. 后续新增权限时按后台要求再次发布。使用测试应用/测试企业时，测试身份与正式身份分别配置，不能把测试联调成功等同于正式版权限已生效。

发布及审批示例见 [飞书官方应用发布操作](https://www.feishu.cn/practice_template/51118)。

### 7. 在本机终端输入凭据并完成浏览器登录

打开 Windows Terminal 或 PowerShell，在你的电脑上执行：

```powershell
Set-Location 'C:\Users\zero\Desktop\Memory-Project\Memoria'
.\.venv\Scripts\python.exe scripts/configure_feishu.py --user qin
```

按顺序操作：

1. 出现 `Feishu App ID:` 时，粘贴后台的 App ID，按 Enter。
2. 出现 `Feishu App Secret (hidden):` 时，粘贴 App Secret，按 Enter。输入时通常不显示任何字符或星号，这是隐藏输入的正常表现。
3. 官方登录工具会打开浏览器；若没有自动打开，按终端显示的授权 URL 在本机浏览器访问。授权 URL 也留在本机，不粘贴到聊天。
4. 登录具有目标文档权限、且在应用可用范围内的飞书账号，确认应用名称及请求范围，然后同意授权。
5. 浏览器回调页显示成功、可关闭页面后，回到终端确认命令结束。

已安装的 0.5.1 本机授权处理器有约 **60 秒**的回调等待时间。可以先登录飞书浏览器页面、准备好凭据，再执行命令；超时则用后文的重新登录命令再试。

凭据写入被 Git 忽略的 `data/users/<UUID>/secrets/feishu.credentials.dpapi`，以当前 Windows 用户 DPAPI 加密，目录仅当前 Windows 用户与 SYSTEM 可读。launcher 只通过子进程环境传给官方工具；OAuth token 由官方 AES 加密存储和系统凭据管理器保护，使用该账号专属 profile，不写入服务器配置或检查报告。不要将真实值填到提交用的示例文件或聊天中。[官方 CLI 的 login / 环境变量参数](https://github.com/larksuite/lark-openapi-mcp/blob/main/docs/reference/cli/cli.md)

如果你原本已有私密 JSON 配置，字段为 app_id、app_secret，可用绝对路径读取；路径需要替换为你的真实文件位置：

```powershell
.\.venv\Scripts\python.exe scripts/configure_feishu.py --user qin --credentials-file 'C:\私密目录\feishu.json'
```

已有本项目凭据，仅重新授权时执行：

```powershell
.\.venv\Scripts\python.exe scripts/configure_feishu.py --user qin --login-only
```

### 8. 验证真实业务，再让 Memoria-Agent 使用

授权命令结束后执行：

```powershell
.\.venv\Scripts\python.exe scripts/check_mcp.py --user qin --server feishu
```

当前检查会初始化官方 MCP，发现工具，并以用户身份调用知识库空间列表与云盘文件列表 API。成功记录应包含：`initialized: true`、`business_call_ok: true`、`response_valid: true`，命令退出码为 0。返回空列表可能只是该用户没有可见知识库，并不必然是授权失败。

报告保存在 `data/users/<UUID>/reports/mcp-check.json`；检查脚本不保存文档正文或 token。它只验证该业务调用，不代表已测过所有 4 个工具或所有文档。本机检查结果为 0 个可见空间、24 个云盘文件；不保存文件名称或正文。

configure_feishu 在官方登录命令返回成功后会将 `config/mcp.toml` 中本人UUID的enabled_servers加入feishu；**仍需以 check_mcp 结果确认实际访问**。然后：

- Memoria-Agent 尚未运行：正常启动服务，启动时加载配置。
- 服务已经运行：登录 qin 后，在 `/docs` 使用当前用户会话，执行 `POST /api/mcp/reload`，再查看 `GET /api/mcp`。不要用 App Secret 或旧全局 API Token 代替登录令牌。

具体注册/登录、Bearer 和 qin 密码私密设置见 [用户隔离说明](用户登录与数据隔离.md)。新账号默认不配置 MCP；管理员必须明确指定账号执行 `configure_mcp.py --user USERNAME`，其飞书授权与数据存储独立。

若服务端口已修改，用实际端口。返回的 feishu 状态应 connected 为 true，工具列表含 4 个允许的读取工具。只有之后实际读取目标文档成功，才能证明该文档的权限也已配置正确。

### 9. 常见问题与排查顺序

| 现象 | 检查/处理 |
| --- | --- |
| 本地飞书设置中找不到 App Secret | 用浏览器打开开发者后台 → 你的自建应用 → 凭证与基础信息 |
| 后台看不到应用或 Secret | 确认企业/账号、应用管理权限；若只有群 Webhook，创建开放平台自建应用 |
| App ID 无效 | 确认复制的是 cli_ 开头的 App ID，不是 open_id、user_id、机器人 ID 或 App Secret |
| App Secret 无效 | 确认它属于同一个 App ID；如果已重置，重新运行输入凭据命令保存新值 |
| 重定向 URL 有误 / redirect_uri 不匹配 | 核对后台完整地址为 http://localhost:3000/callback，保存后重新授权 |
| scope 未开通 / 权限不足 | 按报错核对用户身份权限、正式版发布与当前账号可用范围；新增范围后重新授权 |
| 应用不可用 / 用户不在范围内 | 在应用可用范围加入该授权账号，并确认版本及管理员审核已生效 |
| 浏览器 localhost 拒绝连接 | 确认授权命令正在本机运行，且未超时退出；不是另一台电脑或远程容器 |
| 3000 端口占用 / EADDRINUSE | 先用下面命令确认占用进程，再正常退出那个程序，重新授权；不要随意结束陌生进程 |
| Python 路径不存在 | 确认已进入仓库根目录且项目 .venv 存在，按项目 README 配置环境 |
| 找不到 Node.js 或官方 CLI 文件 | 确认 node --version 可运行、.mcp 依赖已安装；必要时重新执行 install_mcp.py |
| 登录后知识库列表为空 | 检查授权账号是否属于目标知识库；空列表不等于 API 调用失败 |
| 知识库验证通过但目标文档仍读取失败 | 检查该账号对具体文档的分享/访问权限，以及文档类型；本项目正文工具针对 docx |
| 重载项目 API 返回 401 | 先登录 Memoria 账号并携带用户 Bearer 或 Cookie；这与飞书 App Secret 是两套凭据 |
| Token 过期 / 授权撤销 / 新增 scope | 检查 offline_access 及应用权限，再执行 --login-only；Secret 已变则重新输入凭据 |

只读查看端口占用：

```powershell
Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress, LocalPort, OwningProcess
```

排查时可以提供脱敏错误码、阶段和工具名；不要发送 Secret、token、完整授权 URL 或凭据文件。安装完成、OAuth 完成、MCP 连接成功、具体文档读取成功是四个分别验证的状态。

## 主机优化与工程验证

主机最多并行初始化 4 个服务，支持 tools/list 分页、服务端 ping 和 JSON-RPC 请求区分；工具名超长或字符清洗时附加摘要以降低冲突。严格校验开关和超时；超时/取消关闭连接，失效工具移出注册表。Windows 下隐藏服务窗口，并在停止时清理 launcher 所属进程树。

启动日志不打印凭据命令或上游 stderr 正文，检查报告仅保留身份、状态、列表数量及公开搜索链接。工程验证见 `tests/test_mcp_lifecycle.py`；真实联网验证见 `scripts/check_mcp.py --user qin`，两种验证结果分别记录。

2026-10-11 实现基线 `040362c` 的本机 Node.js 为 22.16.0：搜索返回 3 个实际链接，GitHub get_me 确认 ZJ331456 且本仓库 README 读取成功；飞书已完成 OAuth，空间列表成功（0 个可见空间）、云盘列表成功（24 个文件）。项目 Host 的 qin 配置实际注册搜索 2 个/GitHub 8 个/飞书 4 个允许工具，并经 ToolRegistry 成功调用飞书空间列表；记录在该账号 `reports/mcp-host-check.json`。上述调用都没有验证文档正文或知识库节点读取，不能把四工具白名单都记成业务已测。

这轮完成的接入状态已同步 [研究计划的进度表](../plans/长期陪伴记忆研究与实验计划书.md#当前进度与下一步)。后续每次新增权限、重新授权或读取新文档，应更新实际检查时间、工具和结果，不能沿用旧成功记录充当新验证。

## 其他免费候选：仅推荐，未部署

以下项目开源、可本地运行，基础使用无需购买 MCP 服务/API key；本地计算、目标站点账号和网络成本仍由使用者承担。

| 候选 | 能力与建议 |
| --- | --- |
| [Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp) | 浏览器自动化，适合必须交互才能访问的网页；比普通搜索更耗资源，按需再接入 |
| [官方 Filesystem MCP](https://github.com/modelcontextprotocol/servers/blob/main/src/filesystem/README.md) | 文件读取/搜索，适合限定资料目录；接入时用明确目录和只读工具白名单，当前客户端不宣告 roots 支持 |
| [官方 Fetch MCP](https://github.com/modelcontextprotocol/servers/blob/main/src/fetch/README.md) | 将网页内容转为模型易读文本，与现有 fetchWebContent 有重叠；只有需要其格式化能力再增加 |

没有默认增加第二套“记忆 MCP”：当前任务应验证本项目的来源、纠正与长期状态管理，额外记忆库会造成事实归属与评测边界混淆。

候选发现入口：[mcp.so 中文目录](https://mcp.so/zh)、[mcp-cn](https://mcp-cn.com/)。目录收录不等于已安装、免费无限制或业务验证成功。

2026-10-16四类配置迁移后，三个实际安装路径均核对存在；新mcp.toml下三服务真实初始化/只读业务检查通过（搜索返回链接、GitHub身份/仓库读取、飞书知识空间/云盘列表）。安装目录仍在.mcp，不以配置模板替代服务。没有发送飞书消息或执行外部写操作；此为接线验收，不是记忆质量评测。
