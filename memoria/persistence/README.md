# SQLite 持久化适配器

[核心包](../README.md) · [统一入口与存储边界](../../docs/implemented/统一内核入口与存储边界.md)

API、机器人传输和账号公共模块通过具名仓储操作访问数据，不写表查询。此目录没有新的数据库或配置目录，仍使用既有各账号 SQLite、账号库和渠道状态库。

| 文件 | 责任 |
| --- | --- |
| [accounts.py](accounts.py) | SQLiteAccountRepository：账号、密码摘要、角色、可撤销登录、限流与并发校验；公共 AccountStore 导出保持 |
| [channels.py](channels.py) | SQLiteChannelRepository：一次性绑定、收件箱、游标、重放/重发保护和延迟记录；公共 ChannelState 导出保持 |
| [studio.py](studio.py) | 当前租户的只读概况、原话分页/搜索、长期记忆和处理状态投影 |
| [formation.py](formation.py) | 当前有效 Observation/Pattern、资料清单及导入回执，只读且检查来源 |
| [reviews.py](reviews.py) | 修订目标投影、模式关联及 pending 候选 CAS 编辑；发布策略仍在记忆领域 |
| [library_records.py](library_records.py) | 资料原始版本和 SHA 复核，供资料及实体领域共用 |
| [memory_trace.py](memory_trace.py) | 生命周期触发器、事务水位及有界跨来源lineage投影；Observability不直接查询领域表 |

调用方必须传入服务器为当前账号选择的 Store，不能按请求路径任选数据库。写操作保留原锁和事务；仓储不得将 SQL 异常或私密字段直接当成对外诊断输出。Claim/治理等领域 SQL 仍在原领域模块，尚不支持直接切换 PostgreSQL；需要新后端时应实现对应操作与事务契约，再通过完整验证。
