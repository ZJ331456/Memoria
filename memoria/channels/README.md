# 外部通讯渠道

消息来源由服务端provenance上下文逐条写入message_origins，模型与存储链路统一；同会话网页续聊也记录真实入口，旧未记录保持未知。Chat/Studio显示具体渠道与异步提取/审核状态，任务完成不等于产生候选或入库。

[核心包](../README.md) · [统一配置与接入](../../docs/implemented/飞书QQ与Telegram通讯接入.md) · [飞书详细说明](../../docs/implemented/飞书机器人对话接入.md)

飞书、QQ官方C2C和Telegram私聊复用当前账号TenantPool/chat_turn、Reader和记忆机制。每平台分别凭正式网页登录的一次性码绑定，不按应用、用户名、手机号或消息里自述身份选择账号；新注册用户不会获得他人渠道绑定。机器人收消息与MCP工具授权独立。

| 文件 | 职责 |
| --- | --- |
| base.py | 共用绑定/命令/账号路由、全局模型并发、生成及有限投递 |
| state.py | 平台独立SQLite、绑定码摘要、去重/游标/发送状态和按账号的阶段耗时 |
| polling.py | QQ/Telegram后台生命周期、入箱/完成唤醒、独占监听与关闭 |
| normalize.py / errors.py / config.py | 入箱前消除绑定码原文、脱敏错误、统一配置校验 |
| feishu.py / feishu_transport.py / feishu_api.py / feishu_state.py | 飞书官方SDK子进程、REST、事件适配 |
| qq.py | QQ官方AccessToken、C2C REST、WSS鉴权/心跳/重连与会话恢复 |
| telegram.py | 独立轮询/发送连接池、官方typing状态、持久化offset、Token URL脱敏及不确定发送保护 |

开关、凭据引用和代理统一在config/bot.toml，实际秘密Windows DPAPI保存到data/secrets；代码目录不含私密值。状态/绑定接口为/api/channels/{feishu|qq|telegram}，只取正式登录账号。手机群聊、频道、语音、图片与文件暂不进入个人记忆。

统一后台启动/关闭会同时管理三个渠道。QQ使用原消息ID+序号的被动回复；Telegram没有客户端幂等键，发送结果不确定时停止自动重发，避免重复泄露或重复答案。两者都不因投递失败再次生成。详细限制和真实/工程验收见实现文档。
