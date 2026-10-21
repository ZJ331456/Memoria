# Memoria

**面向长期陪伴与任务经验、以可追溯证据为基础的记忆系统。**

[English](README.en.md) · [快速开始](#快速开始) · [系统架构](#系统架构) · [评测结果](#locomo-全量评测) · [文档](docs/README.md)

Memoria 管理对话、笔记、文档和任务结果的来源、审核、条件与版本，让有限上下文中的回答和后续任务使用适用的记忆。个人事实与资料知识分别管理，项目提供独立 Chat、Memory Studio、HTTP API 与研究工具，当前后台使用 DeepSeek 大模型与本地 BGE Embedding；也支持本地 Qwen 和兼容 OpenAI 接口的模型配置。

![Memoria — Memory that stays in context.](assets/images/memoria-overview.png)

> 当前是本机研究原型与受控试用基础。真实连续人物的长期效果、正向画像视图收益和正式商业发布仍待验证。

## 项目关注什么

| 能力 | 当前实现 |
| --- | --- |
| 独立记忆核心 | [MemoryKernel](docs/implemented/独立记忆核心与多来源接口.md)提供来源存取、统一证据检索与依赖校验；Chat、文档和任务应用共用，保留个人事实与资料边界 |
| 可追溯的记忆 | 保存原话与来源定位；完整支持组、候选审核、纠正、撤销和不可变修订 |
| 记忆形成与巩固 | [有界历史上下文与关联核验](docs/implemented/有界历史上下文与观察关联.md)辅助Observation；单次行为先观察，跨会话积累Pattern并独立审核，支持受约束演化及核心降级 |
| 非对话资料与经验 | [TXT/Markdown/Word DOCX等文本导入](docs/implemented/文本解析与跨资料记忆形成.md)，保存原件与位置；来源感知观察、跨资料巩固、完整支持组和受约束版本演化；本人事实与外部知识分开 |
| 生产闭环诊断 | [按来源前缀重执行](docs/implemented/生产记忆闭环与逐题归因.md)形成、审核、Claim/View或资料Reader；[限额真实调用](docs/implemented/生产记忆闭环与逐题归因.md#claim与view真实调用补测2026-10-20)已覆盖Claim自动发布、冷/热View和撤回失效，不改写LoCoMo冻结成绩 |
| 模拟功能验收 | [27组/37项固定检查](simulation/README.md)，最终完整工程1205项、真LLM模拟37/37、本地BGE4/4通过；[条件发布、来源强化与完整验收](docs/implemented/模拟场景功能验收与缺口.md#条件自动发布与独立来源强化2026-10-20)，不作为真实长期效果或研究分数 |
| 适用的状态 | Claim、条件视图、明确会话临时范围与双时间；区分当前、历史和未知 |
| 有界的访问 | 普通记忆混合检索；原始经历BM25及可选Dense/RRF、邻块展开、预算和依赖复核 |
| 受约束的更新 | 非精确关系先筛主体/属性/条件/时间，未知关系不自动覆盖旧记忆 |
| 独立的使用入口 | Chat 负责对话，Studio 负责资料、审核与来源回查；账号数据隔离 |
| 可核对的研究 | 事务水位、冻结副本/固定池重放，记录来源/模型、实际调用、失败、费用下界与指纹 |

[本批次机制、受限两题诊断与剩余边界](docs/implemented/生产重放与普通来源支持.md)分别记录实现和效果，未更新下方冻结全量成绩。

## 系统架构

![How Memoria works — identity, memory runtime, models and storage](assets/images/memoria-architecture.png)

Claim 按槽位与条件访问，普通记忆使用混合检索；充分性与风险核验通过显式开关启用。完整边界见[系统实现](docs/implemented/系统架构与实现说明.md)。[英文宣传海报 PNG](assets/images/memoria-overview.png) · [架构图矢量 SVG](assets/images/memoria-architecture.svg)

## 快速开始

以下命令面向 Windows PowerShell。本机验证环境为 Python 3.13.5、Node.js 22.16.0；依赖安装到项目 `.venv` 和 `frontend`。

### 1. 准备环境

```powershell
git clone https://github.com/ZJ331456/Memoria.git
Set-Location Memoria
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Get-ChildItem config/*.example.toml | ForEach-Object {
    $taskConfigFile = $_.FullName.Replace('.example.toml', '.toml')
    if (-not (Test-Path -LiteralPath $taskConfigFile)) {
        Copy-Item -LiteralPath $_.FullName -Destination $taskConfigFile
    }
}
```

已有项目直接进入仓库并保留现有环境、私密配置和账号资料。

Python 依赖统一在 [requirements.txt](requirements.txt)，按服务、本地模型、向量扩展、飞书、评测、图表和测试分类注释；上述命令安装全部类别。CUDA 用户应先按[配置指南](docs/implemented/安装配置与启动.md#依赖清单与平台脚本)选择官方 PyTorch wheel，再安装统一清单。

### 2. 配置模型

| 方案 | 需要准备 |
| --- | --- |
| 远程 LLM + 本地 BGE | 在独立模型文件配置 main / fast 的 OpenAI 兼容 API；BGE 使用 `backend="bge"`、本地路径和 device |
| 本地 Qwen + BGE HTTP 服务 | 安装统一依赖清单、准备本地权重，先启动独立的 8080 模型服务 |

运行配置统一在[config](config/README.md)：system.toml管系统，models.toml统一LLM的API/本地选择及Embedding，bot.toml统一飞书/QQ/Telegram机器人，mcp.toml统一MCP定义与账号启用。四份实际文件都忽略，仅提交对应example，没有local子目录。账号专属模型也在models.toml按UUID区分，加密凭据和OAuth仍保存在本人私密数据目录。使用项目venv执行`python scripts/configure_models.py show --user qin`查看实际来源，0推理且不显示Key。[配置与调用方式](docs/implemented/配置目录与模型后端.md) · [安装指南](docs/implemented/安装配置与启动.md)。

### 3. 构建并启动

```powershell
.\scripts\cmd\build_frontend.cmd
.\scripts\cmd\start_backend.cmd
# 查看状态 / 停止
.\scripts\cmd\start_backend.cmd -Action Status
.\scripts\cmd\stop_backend.cmd
```

Windows 入口集中在 [scripts/cmd](scripts/cmd/README.md)，可直接双击；[scripts/sh](scripts/sh/README.md) 为 Linux 预留，当前尚未实现启动脚本。

| 入口 | 地址 |
| --- | --- |
| Chat | http://127.0.0.1:2237/ |
| Memory Studio | http://127.0.0.1:2237/studio/ |
| API 文档 | http://127.0.0.1:2237/docs |

Chat 和 Studio 共用正式登录/注册，项目不提供默认密码。登录后点击用户名账号菜单（Chat左下角、Studio右上角），可切换账号、退出、连接飞书或[修改自己的密码](docs/implemented/用户登录与数据隔离.md#当前账号自助修改密码2026-10-15)，成功后重新登录。Chat采用用户右侧、助手左侧布局，支持Markdown阅读、复制与原文切换。首次账号、开发模式、端口和日志见[启动指南](docs/implemented/安装配置与启动.md)与[后台管理](docs/implemented/本机后台启动与停止.md)。

Studio按日常管理与高级诊断分组。“记忆处理与服务”解释处理进度与当前策略；默认LLM审核低风险候选并自动入库，人工辅助处理敏感、冲突和证据不足。历史原话可点击“LLM提取并处理”，成功后查看长期记忆及版本。公开资料不升格为本人事实，详见[自动审核与人工辅助](docs/implemented/LLM自动审核与人工辅助.md)。结构化Claim、双时间、检索与评测账本的用途和资料前提见首页功能地图。

MCP 工具和[飞书私聊](docs/implemented/飞书机器人对话接入.md)均为可选接入；飞书需正式账号绑定，MCP 文档授权与机器人消息接收分别配置。

## LoCoMo 全量评测

最新公开完整比较使用 **10 段对话、5,882 条原话、1,986 道 QA**，Reader 为 DeepSeek `deepseek-flash` 非思考模式，Embedding 为本地 `bge-small-en-v1.5`。以下是冻结的 v2 历史成绩；后续工程修复未重新运行全量评测。

| 方法 | 普通 Answer F1 ↑ | 对抗拒答短语分 ↑ | 已知 Token / 题 ↓ |
| --- | ---: | ---: | ---: |
| RRF + 邻句，首次回答 | 56.12% | 94.17% | 3,986.38 |
| RRF + 全历史复核 | 58.65% | 92.38% | 18,414.96 |
| RRF + 定向补检 | 57.47% | 92.83% | 7,290.80 |
| 充分性 + 风险核验 + 自适应补检 | 51.15% | 95.96% | 12,663.62 |

普通题 1,540 道，对抗题 446 道，分别计分。拒答短语分与 F1 均不是人工语义准确率；Token 是包含失败/重试的已知下界，未知 usage 不计零成本。四方法预算不同，没有同协议外部方法排名；自适应组最终 1 题工程失败保留完整分母。

**综合质量、拒答与成本目标尚未达到，生产默认保持。** [完整指标与指纹](assets/evaluation/locomo-risk-full-20261014.json) · [协议、失败与限制](docs/implemented/证据充分性与风险感知回答.md#v2完整1986题消融2026-10-14)

<details>
<summary>查看质量与成本图，以及其他保留实验</summary>

![LoCoMo四方法全量比较](assets/evaluation/locomo-risk-full-20261014.png)

当前展示保留三组结果：LoCoMo 完整四方法、LCCC 固定 21 轮、同池证据选择。旧冒烟、开发子集和重复导出已从当前资产移除，历史版本与本地原始账本可追溯。[实验资产与复现](assets/evaluation/README.md)

</details>

## 仓库目录

```text
Memoria/
├─ config/           # 系统、模型、飞书、MCP各一份私密配置及公开example
├─ eval/protocols/   # 冻结研究登记，与生产配置分开
├─ assets/           # 项目图片、保留评测结果与来源清单
├─ docs/
│  ├─ implemented/  # 已实现行为、操作和验证边界
│  └─ plans/        # 研究路线与待验收事项
├─ frontend/         # chat / studio / shared
├─ memoria/          # 记忆、运行时、模型、API与研究代码
├─ scripts/          # Python/PowerShell工具、模型准备与图表复现
│  ├─ cmd/          # Windows构建、启动、停止入口
│  └─ sh/           # Linux入口预留，尚未实现
├─ tests/            # 工程回归
├─ data/             # 本机账号、原文、配置和实验账本；Git忽略
└─ model/            # 本地模型权重；Git忽略
```

数据目前采用每账号 SQLite/WAL 与本地文件，配置和凭据保持本机隔离。[数据与权限](docs/implemented/用户登录与数据隔离.md) · [来源与许可](assets/data-sources/README.md) · [AI 协作约定](AGENTS.md)

可选使用[DBeaver查看本机数据库](docs/implemented/DBeaver可选数据库查看.md)：按已有账号生成只读连接，检查历史消息、长期记忆、候选和任务。它不参与项目启动，不安装也不影响业务功能。

## 下一步

算法主线：来源感知形成 → 跨会话/跨资料巩固 → 受约束演化 → 同预算质量与成本验证。文本来源、观察、完整支持组及版本机制已有实现；下一步验证真实支持与反证、误合并、错误替代、撤回泄漏和任务复用，不把保存条数或工程测试当作长期效果。已有机制尚未证明同协议算法领先。[当前代码与七个同类项目对照](docs/implemented/算法现状与同类项目对照.md) · [具体交付与消融](docs/plans/下一阶段算法路线与对照实验.md)

真实连续本人效果仍待资料；备份恢复、迁移、账号生命周期和运行监护继续保留。[研究与商用计划](docs/plans/长期陪伴记忆研究与实验计划书.md)
