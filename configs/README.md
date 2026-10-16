# Configs：模型配置与研究登记

[项目首页](../README.md) · [完整配置说明](../docs/implemented/配置目录与模型后端.md) · [研究工具](../scripts/README.md)

| 入口 | 用途与提交范围 |
| --- | --- |
| [config.example.toml](../config.example.toml) → 根config.toml | 系统参数、端口、预算、数据与模型文件引用；真实config.toml忽略 |
| [models.example.toml](models.example.toml) | 公开LLM OpenAI兼容API/回环、本地BGE与Embedding API参数模板 |
| local/models.toml | 本机管理员默认main/fast/embedding；local全目录忽略 |
| local/feishu-bot.json / credentials.dpapi | 机器人启用和加密应用凭据；忽略，不存入研究协议 |
| research-local-roles.toml / research-remote-roles.toml | 历史本地/远程Reader等角色参数；不含Key |
| research-*.json | 冻结研究输入/协议/预算与失败分母，不当生产模型默认值 |

现有账号还会加载data/users/<UUID>/config/models.override.toml及其独立DPAPI profile；更改全局文件不一定覆盖账号值。命令`python scripts/configure_models.py show --user qin`查看实际来源，使用项目.venv，0推理且不打印Key。

新机器复制models.example.toml到configs/local/models.toml，在根config.toml设置models.file引用；现有文件不覆盖。此前data/config的服务器级配置通过显式迁移工具归拢，账号数据与凭据保持独立，详见完整说明。

公开研究登记保留原ID/指纹/失败，不随本轮目录整理重新评分。新增实验使用新evaluation_id与实际代码/模型/输入指纹；原始gold、预测和费用账本仍在本机data，未知保持未知，不用工程替身作研究成绩。
