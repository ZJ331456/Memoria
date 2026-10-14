# 外部消息渠道

[核心包](../README.md) · [飞书配置与边界](../../docs/implemented/飞书机器人对话接入.md)

本模块是消息入口，与MCP工具分开。当前飞书仅接入人类私聊文字，必须通过已登录网页的一次性码绑定到账号；收到消息后通过该账号TenantPool与chat_turn复用Agent，不创建第二套记忆引擎或跳过Reader保护。

| 文件 | 职责 |
| --- | --- |
| feishu.py | 生命周期、单监听租约、账号路由、并发/取消/解绑、保存与回复 |
| feishu_transport.py | 固定官方SDK长连接，私有子进程，持久化入箱后快速ACK，无原始SDK日志 |
| feishu_state.py | 入箱去重、一次性码摘要、绑定、重启中断与有限重发状态 |
| feishu_api.py | 固定飞书HTTPS源、短期tenant token、文本分块、稳定UUID和脱敏错误 |

应用凭据仅Windows DPAPI文件；本地SQLite与用户聊天库仍需本机文件权限和商用备份/加密设计。关闭统一后台也会结束该监听，MCP OAuth不自动成为机器人账号绑定。群聊、附件、语音和多机器队列暂未实现。工程替身仅在tests临时库，手机真实调用单独记录。
