# MCP npm 依赖清单

[上级部署说明](../README.md) · [运行时边界](../../memoria/mcp/README.md)

package.json/package-lock.json固定open-websearch 2.2.0和官方lark-mcp 0.5.1。安装入口为仓库根的`scripts/install_mcp.py`，实际node_modules在.mcp，不在此目录或全局npm。GitHub MCP使用独立官方Windows二进制及checksum核验。

凭据与OAuth profile按账号保存在忽略路径，本目录不能包含Secret。配置/握手/授权/业务验证分别通过configure_mcp、configure_feishu和check_mcp命令执行；[详细步骤](../../docs/implemented/MCP部署与授权.md)。这些版本是当前锁定状态，不代表所有平台和最新发布已验证。
