# DBeaver：可选的本机数据库查看工具

[文档导航](../README.md) · [账号与数据隔离](用户登录与数据隔离.md) · [脚本说明](../../scripts/README.md)

DBeaver用于本机管理员查看SQLite表、检查自动记忆任务和排查来源。它完全可选：不安装、不启动或卸载DBeaver，均不影响Memoria的启动、登录、对话、机器人和记忆处理。后端没有新增DBeaver依赖、数据库端口或服务。

## 已配置的连接

连接直接打开现有SQLite文件，不需要填写Memoria网页账号密码，也不需要MySQL地址/端口。

| DBeaver分组 | 连接 | 文件，相对项目data根 |
| --- | --- | --- |
| Memoria - 01 账号与权限 | Accounts [READ ONLY] | auth/accounts.db |
| Memoria - 02 用户记忆 | Memory - 用户名 [READ ONLY] | users/服务器UUID/memory/memoria.db |
| Memoria - 03 通讯渠道 | Channel - feishu / qq / telegram [READ ONLY] | channels/对应平台.db |

用户库按账号库中的真实UUID定位，不手填或猜测UUID。只为命令明确指定的已有账号生成连接；已有渠道库才添加，缺失数据库不创建。其他账号后续可用同一命令添加。

所有生成连接同时设置DBeaver只读标记、SQLite URI的`mode=ro`、JDBC的`open_mode=1`及初始化`PRAGMA query_only=ON`。保留正常WAL读取，不设置`immutable=1`，因此可以查看已提交的新消息。每次刷新查看最新内容；未提交内容仍不可见。

这是本机文件管理权限：能读取这些文件的Windows用户，可以查看账号库和渠道库；网页退出或切换用户不会切换DBeaver连接，也不构成数据库文件加密。渠道库包含多账号绑定与入箱信息，不能作为普通用户共享入口。密码仅有盐和摘要，DBeaver无需保存网页密码、模型Key或机器人凭据。

## 日常使用

1. 打开DBeaver，展开左侧对应Memoria分组。
2. 双击`Memory - 用户名 [READ ONLY]`，展开Tables，选择表。
3. 双击表，在“数据”页查看；用筛选条件和刷新按钮定位最新记录。
4. 关联查询可打开SQL编辑器；确认工具栏的数据源是目标用户库，`Ctrl+Enter`执行当前语句。

首次连接可能要求下载SQLite JDBC驱动，使用DBeaver默认的官方Maven来源。无需重装整个软件；下载失败优先检查网络或在驱动管理器补齐库。不要选择MariaDB/MySQL来打开`.db`文件。已有其他数据库的驱动弹窗属于该旧连接，不代表Memoria需要它。

配置脚本还将以下SQL说明保存到DBeaver当前项目的Scripts目录；它们不含查询结果或私密对话。

| 脚本 | 选择的数据源 | 内容 |
| --- | --- | --- |
| Memoria-memory.sql | 用户Memory连接 | 会话/原话/记忆数量、任务与审核状态、来源渠道、近期消息与任务 |
| Memoria-accounts.sql | Accounts连接 | 账号ID、角色和登录是否启用；不查密码摘要、盐和令牌 |
| Memoria-channels.sql | 单个平台Channel连接 | 表清单和入箱状态聚合；不查询发送者标识和正文 |

可在DBeaver“文件/项目”视图找到Scripts，也可使用“文件→打开文件”打开这些SQL。脚本默认不会执行，不要一次执行所有用户库、账号库和渠道库的查询；它们属于不同数据库。

## 哪些表值得看

| 目的 | 表 | 说明 |
| --- | --- | --- |
| 历史对话 | sessions、messages | 原始记录，存在消息不等于已形成长期记忆 |
| 消息渠道 | message_origins | 新消息的服务端来源；旧消息缺行时仍未知，不推测为Chat |
| 自动提取进度 | memory_jobs | status、attempts、error、extracted_count、staged_count；提取完成也可能0候选 |
| 候选与审核 | memory_reviews、memory_review_assessments | 候选、审核结果和辅助原因；低风险可经LLM主审自动入库 |
| 普通长期记忆 | memories、memory_support_groups、memory_support_evidence | 已持久化内容与来源支持；状态需结合审核/撤销判断 |
| 结构化事实 | claim_roots、claim_revisions、claim_sources | 根、不可变修订和支持来源；最新一行不一定为有效批准事实 |
| 条件视图 | claim_views | 缓存与依赖，不是另一份权威原始事实库 |
| 渠道运行 | bindings、inbox、channel_timings | 绑定、去重/处理/发送及阶段时间；在对应渠道连接中查看 |

例如，只检查记忆任务，不显示私人正文：

```sql
SELECT status, count(*) AS total
FROM memory_jobs
GROUP BY status;

SELECT id, status, attempts, extracted_count, staged_count,
       datetime(updated_at, 'localtime') AS updated_local
FROM memory_jobs
ORDER BY updated_at DESC
LIMIT 50;
```

记忆批准、纠正、删除、重提取和密码变更继续通过Chat/Studio或正式管理CLI执行。直接改SQLite会绕过来源校验、审计、版本、索引和缓存失效，不能替代产品流程。

## 可复现的配置命令

先启动DBeaver一次，让其创建默认General项目，然后完全关闭DBeaver。**Memoria后台可以继续运行。** 从仓库根执行：

```powershell
# 仅预览：读取现有库、验证目标，不修改DBeaver配置
.\.venv\Scripts\python.exe scripts/configure_dbeaver.py --user your_username

# 保存配置；重复--user可选择多个已有账号
.\.venv\Scripts\python.exe scripts/configure_dbeaver.py --user your_username --apply

# 自定义DBeaver工作区
.\.venv\Scripts\python.exe scripts/configure_dbeaver.py --user your_username --workspace 'D:\DBeaverWorkspace' --apply
```

脚本读取现有`config/system.toml`所选数据根；可用`--config`指定系统配置。它不修改四类实际配置，不新建另一份模型/机器人配置，也不修改项目启动脚本或Python依赖。

默认保存位置：

```text
%APPDATA%/DBeaverData/workspace6/General/
  .dbeaver/data-sources-memoria.json
  .dbeaver/memoria-backups/时间戳/   # 覆盖本工具旧文件前才产生
  Scripts/Memoria-memory.sql
  Scripts/Memoria-accounts.sql
  Scripts/Memoria-channels.sql
```

普通`data-sources.json`和其他连接不覆盖；重复执行内容相同时不写文件、不新增备份。脚本在DBeaver运行时拒绝应用，避免工作区覆盖；当前自动配置仅支持Windows。实际连接文件含本机路径，留在软件工作区，不上传Git；公开仓库只包含通用脚本与本文。

## 常见问题与移除

- **看不到新消息：** 确认选择正确用户库，刷新数据页；数据库是按UUID分开的，不能打开旧根库或其他账号库。
- **显示只读：** 这是预期配置。查看不需要关闭只读；需要业务更改请回Studio。
- **no such table：** 先确认SQL与连接对应。账号库没有messages，渠道库没有memories；未初始化过的旧账号库也可能缺少新业务表。
- **提示sqlite扩展或向量表模块缺失：** 普通表可先查看；DBeaver JDBC不保证自动加载后端的向量扩展。不要改表或删向量索引，完整检索使用Studio/API。
- **database is locked：** 结束自己的长事务或断开再连接；生成配置采用自动提交与3秒等待。不要删除`-wal`、`-shm`或强行终止业务写入。
- **想备份：** 运行中的`.db`旁可能有WAL，不能把单独复制主文件当完整备份。应使用SQLite备份接口或先正常停服后备份完整数据库。
- **不想使用DBeaver：** 直接关闭或正常卸载软件即可，项目不会受影响。只撤销本次连接时，先退出软件，在工作区移走`data-sources-memoria.json`和三份Memoria SQL即可；无需删除任何`data/`数据库。

## 本机验证与边界（2026-10-16）

已识别本机DBeaver 26.2.2，为明确选择的已有用户和三个已有渠道生成5个只读连接，保留原有非Memoria连接。SQLite JDBC 3.53.4.0从Maven Central下载，SHA256校验通过；DBeaver界面已连接选定用户的记忆库，后续界面操作因用户按Esc停止。

另使用DBeaver自带Java、同一SQLite JDBC与SLF4J库，从命令行连接全部5库，实际查询表清单数量，5次均确认JDBC的isReadOnly=true。三份SQL通过SQLite只读连接合计执行15条。其他四个连接未逐个GUI点击，这与JDBC连接验证分别记录。

工程验证覆盖WAL已提交记录可见且拒绝写入、未知账号/缺库不创建、异常账号ID不越界、保留无关连接、旧生成配置备份和幂等应用。完整记录见[研究计划第48节](../plans/长期陪伴记忆研究与实验计划书.md#48-dbeaver可选本机查看2026-10-16)。未执行生产抽取/审批、收费模型调用或新LoCoMo评测。

这只是本机查看与排障能力，不代表已完成商业备份恢复、存储迁移、多机并发或长期记忆效果验证。

参考：[SQLite连接](https://dbeaver.com/docs/dbeaver/Database-driver-SQLite/)、[多文件预配置](https://dbeaver.com/docs/dbeaver/Admin-Manage-Connections/)、[连接JSON字段](https://dbeaver.com/docs/dbeaver/Data-Sources-Json-Reference/)、[SQLite WAL](https://www.sqlite.org/wal.html)。
