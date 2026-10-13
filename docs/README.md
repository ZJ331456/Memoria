# Memoria-Agent 文档入口

更新：2026-10-13。文档分为**已实现说明**与**待实现计划**；项目概览、启动命令和LoCoMo全量分类表见[根README](../README.md)，具体功能从下表查阅。源码/真实验证决定状态，计划与工程测试不作为研究收益。

## 已实现的功能说明

| 文档 | 内容 |
| --- | --- |
| [系统架构](implemented/系统架构与实现说明.md) | 运行时、存储、记忆链路与模型职责 |
| [API](implemented/API接口文档.md) | 正式登录网关与个人/共享/研究接口 |
| [用户隔离](implemented/用户登录与数据隔离.md) | UUID独立库、Cookie/账号、MCP、旧历史与文件保护 |
| [Memory Studio](implemented/MemoryStudio与统一记忆访问.md) | 来源审核、双时间、检索解释、只读账本、统一访问/有限解析 |
| [Claim与条件视图](implemented/Claim条件视图与访问路由.md) | 来源/批准、不可变修订、双时间、条件、缓存依赖 |
| [Claim演化策略](implemented/Claim证据核验与演化策略.md) | 独立核验/关系/策略、影子建议、目标复核与明确默认访问 |
| [条件时序与证据选择](implemented/条件时序抽取与预算证据选择.md) | 有限条件继承、事件精度、自然语言双时间、四策略/预算、真实同池诊断及未获收益 |
| [Reader预算与引用](implemented/完整Reader预算与生产引用校验.md) | 完整请求计量、可信本地Token、远程字节门槛、缓冲回答与来源/版本引用校验 |
| [LoCoMo全量评测](implemented/LoCoMo全量评测与英文检索修复.md) | 完整1,986题、首次/复核分类F1、对抗拒答退步、英文检索与工程故障审计 |
| [LoCoMo十题诊断](implemented/LoCoMo十题真实调用诊断.md) | 官方SHA、独立benchmark、10次真实reader、34%→22%证据召回与英文词面无命中 |
| [分层与配置](implemented/记忆分层与配置.md) | 工作/情景/语义/程序层、TTL与预算 |
| [审核与来源](implemented/自动记忆审核与来源定位.md) | 自动候选、显式审核和原话回查 |
| [版本与检索](implemented/记忆一致性与检索审查.md) | 更正、撤销、时间区间与向量命名空间 |
| [共享治理](implemented/记忆治理与多Agent共享记忆.md) | 空间ACL、提案、CAS和事件 |
| [Embedding](implemented/本地Embedding配置.md) | 项目BGE、下载指纹、.venv与编码策略 |
| [模型统一接口](implemented/本地与远程OpenAI统一接口.md) | Qwen/BGE HTTP、进程与远程模式 |
| [分块与记录](implemented/实验记录与长记忆分块索引.md) | 长文完整span、父/块一致性、命中片段、阶段计时 |
| [MCP与飞书授权](implemented/MCP部署与授权.md) | 搜索/GitHub/飞书、私密配置、OAuth和只读验证 |
| [研究工具](implemented/真实历史标注与评测.md) | 来源/授权/AI/真人标注、冻结、runner与独立scorer |
| [固定21轮自动评测](implemented/DenseRRF自动评测与视图性能.md) | 来源召回、真实reader、失败/成本、空状态副本基准 |
| [旧六轮QA](implemented/真实QA对照评测结果.md) | 窗口/compact历史版本，不混入21轮 |
| [公开来源](implemented/真实对话数据与实验入口.md) | LCCC50段/206原话、回执与归属边界 |
| [GitHub规范](implemented/github提交规范.md) | AI读取、main、ZJ331456与普通推送 |

## 待实现计划

| 文档 | 用途 |
| --- | --- |
| [研究与实验计划 v15](plans/长期陪伴记忆研究与实验计划书.md) | 算法/真实数据/B0–B7、第18节商业验收、第19–23节算法、Reader契约与LoCoMo完整评测及下一步 |
| [研究来源](plans/研究来源.md) | 一手机制与外部数据适用边界，不是项目实测成绩 |
| [计划目录](plans/README.md) | 文档分工与人工暂停要求 |

当前重点：LoCoMo全量已完成，下一步优先query-only风险门控与回退后支持度、多跳/开放域及时间精度，再做可信本地Reader同Token预算和独立留出对照；商用备份恢复、迁移与账号生命周期按第18节并行。真实Claim/条件/更新F1及长期变化仍需新获准连续本人资料，独立真人研究评估暂缓，生产明确审批保留。

## 评测与验证状态

- **当前LoCoMo全量：** 10段长对话/5,882条原话/1,986题（1,540普通、446对抗），最终执行错误0。普通F1首次56.12%、复核后58.65%；对抗官方拒答短语分94.17%→92.38%。[首页分类表](../README.md#locomo-全量评测)与[完整说明](implemented/LoCoMo全量评测与英文检索修复.md)区分方法、预算和指标；不是语义准确率或真实长期效果。
- **历史LCCC开发评测：** 50段匿名短对话/206消息、64道AI辅助QA；固定21轮为768检索题次/576实际reader调用。字面匹配与LoCoMo官方F1不可直接比较，原失败、图表和指纹在[历史报告](implemented/DenseRRF自动评测与视图性能.md)及[资产目录](assets/evaluation/README.md)保留。Window/Dense/RRF是方法，不是三个数据集；20,000条下载归档未全量评估。
- **视图性能边界：** 真实账号副本0已批准Claim、5,400次空状态访问全为clarify；只测空缓存开销，正向画像视图收益与生产HTTP SLO未知。
- **工程验证：** TLS/续跑版本全量525 passed / 199.78s，后续失败恢复受影响24 passed / 11.42s。工程通过不转换为研究正确率；完整预算与有限引用ID校验不证明语义蕴含。已有前端构建/真实HTTP验收见[Studio说明](implemented/MemoryStudio与统一记忆访问.md)。
- **首页整理：** 按用户最新要求删除README中的LCCC对比和当前能力章节，英文首页同步移除旧评测对比段落；首页保留项目概览、全量分类表、启动、实际存储及下一步，具体功能与旧实验记录由本导航和实现文档查阅。此项仅文档维护，0新增模型调用/0新评分，冻结聚合与历史产物未改。

## AI读取与维护

先读[AGENTS.md](../AGENTS.md)，按模块读取说明，提交前读GitHub规范。支持AGENTS的客户端会自动获得根约定，其他客户端应显式读取；不通过隐藏钩子自动发布，推送始终依用户授权。

每项能力变更在同次更新implemented、计划状态/证据/未完成/下一项验收、模块README和本页。密码/Secret/token只记录配置状态，不记录值；私人来源/gold/数据库/模型不提交。界面只显示当前账号数据，公开语料不成为qin画像，旧个人历史仍不导入。

## 当前数据范围

当前保留一个LCCC开发来源子集及一个独立官方LoCoMo benchmark。LCCC归属账号实验空间，不代表本人；LoCoMo全量使用独立冻结inputs/history/gold，不写入生产Claim/记忆，源许可为CC-BY-NC-4.0。来源清单见[datasets](../datasets/README.md)，数量/指纹与评分分别见[资产导航](assets/evaluation/README.md)。十题诊断、初始连接失败与完整运行的继承/重试记录全部保留，旧个人历史仍不导入。
