# Deploy：现有部署资产

[根启动命令](../README.md) · [MCP说明](../docs/implemented/MCP部署与授权.md)

目前tracked资产只有[mcp](mcp/README.md)的npm依赖/lockfile，供install_mcp.py复制安装到项目.mcp。这里没有完成商业容器部署、Windows服务守护、反向代理TLS、多进程worker协调或升级回滚流程。

本机业务用项目.venv启动main.py，模型服务可独立8080，Studio构建后由2237同源托管。先选择单机部署目标，再按[商用准备](../docs/plans/长期陪伴记忆研究与实验计划书.md#18-下一阶段功能优先级与商用准备)补备份、资源配额、HTTPS可信代理、readiness/告警与恢复演练；Docker/Kubernetes不是当前既有能力。
