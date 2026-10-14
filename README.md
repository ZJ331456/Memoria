# Memoria

**面向小模型长期陪伴的、以原话证据为基础的记忆系统。**

[English](README.en.md) · [快速开始](#快速开始) · [系统架构](#系统架构) · [评测结果](#locomo-全量评测) · [文档](docs/README.md)

Memoria 将原话、审核、条件和版本连接起来，让有限上下文中的回答能够使用有来源、在当前情境下适用的记忆。项目提供独立 Chat、Memory Studio、HTTP API 与研究工具，支持本地 Qwen/BGE 和兼容 OpenAI 接口的远程模型。

![Memoria — Memory that stays in context.](assets/images/memoria-overview.png)

> 当前是本机研究原型与受控试用基础。真实连续人物的长期效果、正向画像视图收益和正式商业发布仍待验证。

## 项目关注什么

| 能力 | 当前实现 |
| --- | --- |
| 可追溯的记忆 | 保存原话与来源定位；候选审核、纠正、撤销和不可变修订 |
| 适用的状态 | Claim、条件视图与双时间；区分当前、历史和未知 |
| 有界的访问 | 普通记忆 BM25 / Dense / RRF 检索，长文分块、证据预算和发布前依赖复核 |
| 独立的使用入口 | Chat 负责对话，Studio 负责资料、审核与来源回查；账号数据隔离 |
| 可核对的研究 | 冻结来源、协议和模型配置，记录实际调用、失败、费用下界与产物指纹 |

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
if (-not (Test-Path config.toml)) { Copy-Item config.example.toml config.toml }
```

已有项目直接进入仓库并保留现有环境、私密配置和账号资料。

Python 依赖统一在 [requirements.txt](requirements.txt)，按服务、本地模型、向量扩展、飞书、评测、图表和测试分类注释；上述命令安装全部类别。CUDA 用户应先按[配置指南](docs/implemented/安装配置与启动.md#依赖清单与平台脚本)选择官方 PyTorch wheel，再安装统一清单。

### 2. 配置模型

| 方案 | 需要准备 |
| --- | --- |
| 远程 LLM + 本地 BGE | 配置 main / fast 的兼容 API；BGE 权重放入 `model/`，Embedding 使用 `local://auto` |
| 本地 Qwen + BGE HTTP 服务 | 安装统一依赖清单、准备本地权重，先启动独立的 8080 模型服务 |

`config.example.toml` 默认使用第二种方案。模型权重不随 Git 下载；已有账号的模型覆盖配置优先于根配置。安装命令、完整 TOML 示例与安全输入凭据的方法见[安装配置指南](docs/implemented/安装配置与启动.md)。

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

Chat 和 Studio 共用正式登录/注册，项目不提供默认密码。首次账号、开发模式、端口和日志见[启动指南](docs/implemented/安装配置与启动.md)与[后台管理](docs/implemented/本机后台启动与停止.md)。

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

## 下一步

算法主线：来源支持组与撤回后的事实维护 → 原始经历的渐进检索 → 受约束更新和临时条件生命周期 → 同预算质量/拒答校准与有效视图收益。以上为待实现/待验收方向；已有工程机制尚未证明同协议算法领先。[当前代码与七个同类项目对照](docs/implemented/算法现状与同类项目对照.md) · [具体交付与消融](docs/plans/下一阶段算法路线与对照实验.md)

真实连续本人效果仍待资料；备份恢复、迁移、账号生命周期和运行监护继续保留。[研究与商用计划](docs/plans/长期陪伴记忆研究与实验计划书.md)
