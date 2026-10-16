# 配置：按职责集中管理

[项目首页](../README.md) · [配置与模型后端](../docs/implemented/配置目录与模型后端.md) · [MCP安装和授权](../docs/implemented/MCP部署与授权.md)

| 本机实际文件（Git忽略） | 公开模板 | 管理内容 |
| --- | --- | --- |
| system.toml | [system.example.toml](system.example.toml) | HTTP端口、数据库根、记忆策略、Reader预算、任务与安全 |
| models.toml | [models.example.toml](models.example.toml) | main/fast LLM的API或本地调用、Embedding API或本地BGE、账号专属槽位 |
| feishu.toml | [feishu.example.toml](feishu.example.toml) | 机器人启用和加密应用凭据位置 |
| mcp.toml | [mcp.example.toml](mcp.example.toml) | 全部MCP服务定义、工具权限、超时及各账号启用列表 |

四份实际文件直接放在本目录，没有local子目录。只复制example到同名实际文件；已有配置不能覆盖。启动默认读取system.toml及同目录另外三份文件。模型和端口可分别维护，不需要models.file或旧覆盖文件。

```powershell
Get-ChildItem config/*.example.toml | ForEach-Object {
    $taskConfigFile = $_.FullName.Replace('.example.toml', '.toml')
    if (-not (Test-Path -LiteralPath $taskConfigFile)) {
        Copy-Item -LiteralPath $_.FullName -Destination $taskConfigFile
    }
}
.\.venv\Scripts\python.exe scripts/configure_models.py show --user qin
```

用户名参数只选已有账号；新部署替换为自己的用户名。show不调用模型、不打印Key。正式HTTP用户不能改服务器模型。模型文件的accounts表和MCP文件的accounts表由服务器UUID选择；不是把某账号凭据变成全体用户默认。

密钥不是另一套配置：机器人加密凭据在data/secrets，账号模型profile和飞书OAuth在data/users/UUID/secrets。不要把这些加密文件或实际配置提交。Windows本地权限可执行scripts/protect_local_data.ps1加固。

MCP实际程序位于.mcp，配置不等于安装目录；scripts/mcp_server.py以stdio拉起真实服务。旧根mcp_servers.example.json已由本目录模板替代。公开冻结研究登记在[eval/protocols](../eval/protocols/README.md)，不控制生产模型。
