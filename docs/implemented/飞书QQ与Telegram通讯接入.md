# 飞书、QQ与Telegram通讯接入

更新：2026-10-16。三平台均进入同一个Memoria账号运行时，复用聊天、Reader、来源、记忆处理与Studio历史，没有为机器人复制记忆引擎。实现统一在[memoria/channels](../../memoria/channels/README.md)。

## 配置与本机状态

原config/feishu.toml已升级为[config/bot.example.toml](../../config/bot.example.toml)对应的私密bot.toml：

```toml
[feishu]
enabled = false
processing_notice = false
credentials_file = "data/secrets/feishu-bot.credentials.dpapi"

[qq]
enabled = false
processing_notice = false
credentials_file = "data/secrets/qq-bot.credentials.dpapi"
proxy_url = ""

[telegram]
enabled = false
processing_notice = false
credentials_file = "data/secrets/telegram-bot.credentials.dpapi"
expected_username = ""
poll_timeout_seconds = 25
proxy_url = ""
```

本机实际三项已启用；上面仅是公开模板，不含真实AppID、Secret、Token或机器人用户名。应用凭据用当前Windows用户DPAPI保存到data/secrets；通道SQLite在data/channels，实际bot.toml/凭据/对话全部Git忽略。只改配置后应重启后台，不宣称支持热切换。

| 平台 | 接收方式 | 本次真实检查 | 个人实际收发 |
| --- | --- | --- | --- |
| 飞书 | 官方Python SDK长连接子进程 | 鉴权/接入检查、正式长连接在线，既有绑定保持 | 先前用户已确认正常；本轮未重发旧对话 |
| QQ | 官方AccessToken + WSS，订阅C2C事件 | 鉴权/接入点成功，正式READY在线 | 用户已确认绑定且收到普通回复 |
| Telegram | 官方Bot API getUpdates长轮询 | getMe身份匹配、无Webhook冲突、正式轮询在线 | 用户已确认绑定且收到普通回复 |

检查命令只做鉴权/接入点读取，0发消息/0LLM。之后用户本人完成两平台绑定与普通收发，私有账本也有对应会话和成功发送记录；不公开正文或外部身份。这证明接入可用，不证明记忆或回答质量。Telegram本机使用系统已有HTTP代理；代理只写实际配置，其他机器按自身网络填写。

## 初次准备和升级

仅使用项目.venv；websockets固定15.0.1，本机已存在，无需污染base。其他机器安装根requirements.txt。四份配置仍为system/models/bot/mcp，按[配置说明](配置目录与模型后端.md)准备，已有实际文件不覆盖。

已有上一轮feishu.toml的部署，先停止后台，再迁移：

```powershell
.\scripts\cmd\stop_backend.cmd
.\.venv\Scripts\python.exe scripts/migrate_config_layout.py --config config/system.toml
.\scripts\protect_local_data.ps1
.\scripts\cmd\start_backend.cmd
```

迁移保留原飞书表及密文路径，旧文件先备份到data/backups/bot-config-*后退休；不改模型/MCP、账号、绑定或历史，重复执行不覆盖新bot.toml。直接从更老根config.toml迁移也输出新四文件布局。本机本轮已完成迁移。

私密终端配置命令（分别执行需要的平台）：

```powershell
.\.venv\Scripts\python.exe scripts/configure_bot.py --channel feishu
.\.venv\Scripts\python.exe scripts/configure_bot.py --channel qq
.\.venv\Scripts\python.exe scripts/configure_bot.py --channel telegram
# 停用某平台，不删除绑定或历史：随后重启后台
.\.venv\Scripts\python.exe scripts/configure_bot.py --channel qq --disable
# 只读检查，不向外发消息、不调用LLM
.\.venv\Scripts\python.exe scripts/check_bots.py
```

AppID/Secret/Token终端隐藏输入；可选私密JSON --credentials-file，Feishu可用--from-user已有账号复用其应用凭据（不复用OAuth）。本机已有真实配置无需重新输入。旧configure_feishu_bot入口已移除，统一使用configure_bot。

## 分别绑定自己的账号

1. 打开本机Chat根地址或Studio，登录自己的Memoria账号。
2. 点击用户名 → **通讯连接**，选择飞书、QQ或Telegram；等待该平台接收服务在线。
3. 生成绑定码，将完整`/绑定 …`命令只发送给该平台对应机器人。每个平台分别生成和使用，码不能跨平台共用。
4. 收到绑定成功后，发送普通文字；对应会话以平台名称开头，在本人Chat/Studio中查看。

Telegram需先打开机器人私聊并Start，QQ需将官方机器人添加为好友。绑定码十分钟、一次性、服务端只存摘要；重新生成使旧码失效。每个应用内一外部身份对应一个账号，已有其他绑定时先解绑；不按用户名、AppID或消息自述身份映射到qin。网页退出不自动撤销手机授权，需要关闭渠道访问时明确解绑。

| 命令 | 行为 |
| --- | --- |
| /help、/帮助、/start | 绑定与使用说明 |
| /bind CODE、/绑定 CODE | 使用该平台一次性码绑定当前登录账号 |
| /new、/新对话 | 下一句开始新会话 |
| /stop、/停止 | 停止当前生成并清空待处理消息 |
| /unbind、/解绑 | 撤销本渠道绑定，已有聊天保留 |

绑定、状态和解绑为`/api/channels/{channel}`、`/pairing`、`/binding`，channel仅允许feishu/qq/telegram。接口取正式登录身份，不接受调用者指定他人owner；状态不返回凭据或外部用户标识。网页与手机使用相同账号数据边界，账号停用/解绑后不再生成或投递个人答案。

## 平台准备和常见问题

普通消息默认只发送最终答案，不额外发送“已收到，正在处理”。这条提示是本项目原先主动添加的等待通知，并非QQ或Telegram强制要求。需要时可在对应平台表设置processing_notice=true，重启后台生效；通知失败不阻止正式回答。默认关闭不影响收件箱持久化、去重或/stop命令，生成本身仍需等待模型和工具完成；当前手机端没有逐字流式输出，过长答案仍可能拆成多条。

日常咨询不会因缺少个人Claim而固定拒答。自动记忆入口先排除原因分析、建议、计划与“没吃什么”等非疑问表达，再使用有限规则识别个人状态回忆；直接memory-answer API与真正个人事实问题仍执行来源/条件/时间和引用校验。普通回答可以给通用说明，不能把不存在的个人记忆当作事实。该规则仍不是通用语义意图分类器，详见[Reader路由说明](完整Reader预算与生产引用校验.md#6-日常咨询与个人事实查询的入口边界2026-10-16)。

QQ使用**QQ开放平台AppID/AppSecret**，不是普通QQ号/密码。开放平台中开通单聊能力、事件权限和测试/发布可用范围；订阅C2C消息，生产API与WSS目标限定官方HTTPS/WSS域名。本机已收到READY，故本轮不需要公网回调或内网穿透。若出现qq_invalid_session_or_intents，检查权限/发布状态；鉴权错误检查App凭据及平台网络/IP限制。URL内容发送仍受平台消息URL配置限制。

Telegram以Token中的机器人ID及getMe身份核对实际机器人，可选expected_username防止配错。存在Webhook时显示telegram_webhook_active，不擅自删旧Webhook；另一个轮询进程抢占会显示telegram_409。网络无法直连时，在本机bot.toml的telegram.proxy_url填写自己的HTTP(S)代理，不能把代理密码发到聊天或issue。

飞书仍使用原长连接事件/权限与账号绑定，详细步骤见[飞书指南](飞书机器人对话接入.md)。MCP OAuth只是工具授权，不替代任何机器人消息入口。

## 可靠性与边界

每平台独立监听租约和SQLite，原消息持久化去重；入箱固定当时账号与绑定版本，同发送者串行，三平台共用最多4个模型执行槽。绑定尝试5次/十分钟、普通消息30次/十分钟（每平台发送者），跨平台会话按原服务机制处理。

Telegram仅在消息持久化或明确过滤之后推进offset，失败重取靠原消息ID去重。发送成功分段有本地回执；网络结果未知或进程中断在发送边界时不自动重发，状态delivery_uncertain，避免无幂等键的重复答案。调用者可查看本地Chat中已保存的内容。Token必需出现在官方API路径，HTTP日志过滤该路径中的秘密，异常不回显原始请求/响应。

QQ在收到持久化消息后推进当前连接序号，心跳/断线恢复携带当前进程session和seq；重启不承诺补齐所有尚未收到的消息。被动回复使用原msg_id和稳定msg_seq，最多三段答案，长文提示到本地查看；可选等待通知使用独立序号。约五分钟的被动回复窗口耗尽会失败，不转为未授权主动消息，也不会重新生成。

生成失败不自动重新调用模型；投递失败只有限重发已经保存的答案（Telegram未知发送结果除外）。终态入箱清除正文/答案，七天后清理元数据与无主发送回执，原聊天仍在本人数据库。传输重连不宣称跨机器exactly-once；QQ消息本身也受平台配额/审核/时效限制。

当前只处理人类私聊文字；群聊、频道、语音、图像、文件、主动提醒和多机队列未交付。SQLite不是整库加密，磁盘保护及商用恢复继续按研究/商用计划登记。工程夹具不作为真实对话或benchmark。

## 验收依据

三平台与Chat/API正常结束的对话均自动创建同一类提取任务，没有按平台关闭LLM提取。来源现在由受信任入口写入消息级channel；Chat/Studio逐条显示渠道与处理结果，空候选不进入审核，旧渠道未记录保持未知。实现与只读接口见[自动记忆状态](LLM自动审核与人工辅助.md#多渠道自动处理与逐条状态2026-10-16)。

相关配置/渠道/账号/启动回归111项通过；迁移/启动/两平台定向61项通过，追加WSS断线Resume后最终渠道51项通过（有重合），正式网关仅本人平台码断言通过。前端18项与类型/生产构建通过。具体耗时和后续收发状态见[计划44](../plans/长期陪伴记忆研究与实验计划书.md#44-qqtelegram与统一通讯配置2026-10-16)。实际鉴权和在线检查不发送测试消息、不调用收费LLM，未运行LoCoMo新评分。

用户反馈后，等待通知与意图修复两组回归共230项通过，默认关闭提示、咨询正常回答和真实记忆查询继续严格校验均有工程断言。正式后台已重启，三平台在线并保留原绑定、历史与加密凭据；本次部署没有重发真实咨询或新增收费测试，手机修复后新回合仍待日常使用反馈。记录见[计划45](../plans/长期陪伴记忆研究与实验计划书.md#45-渠道等待通知与日常咨询入口修复2026-10-16)。

依据：[QQ官方鉴权](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/api-use.html)、[QQ事件/长连接](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html)、[QQ消息接口](https://github.com/tencent-connect/bot-docs/blob/main/docs/develop/api-v2/server-inter/message/send-receive/send.md)、[Telegram Bot API](https://core.telegram.org/bots/api)。
