# Memoria-Agent

[English](README.en.md) · [文档导航](docs/README.md) · [研究与实验计划](docs/plans/长期陪伴记忆研究与实验计划书.md) · [AI 协作约定](AGENTS.md)

面向小模型长期陪伴的记忆管理项目：保存原话证据、审核候选、管理条件与变化，在有限上下文中访问适用记忆。入口为 HTTP API、Memory Studio 和研究 CLI，本地 Qwen/BGE 与远程模型使用统一的 OpenAI 兼容业务接口。

**当前定位：本机研究原型与受控试用基础。** 已有记忆治理、工程保护与下述LoCoMo全量评测；真实连续人物长期效果、正向画像视图收益及正式商业发布验收尚未完成。下一步算法、存储、安全和运维验收见[研究与商用计划](docs/plans/长期陪伴记忆研究与实验计划书.md#18-下一阶段功能优先级与商用准备)。

## LoCoMo 全量评测

2026-10-13原全量与2026-10-14 P0消融均使用官方 `locomo10.json` **全部1,986题**：10段长对话、5,882条原话，包含1,540道普通题和446道对抗题。Reader为 **DeepSeek `deepseek-flash`（非思考模式）**，Embedding为本地 **`bge-small-en-v1.5`**；仅评测英文文本来源检索与回答，不读取图像，未使用生产Claim/条件视图。

多跳、时序、开放域、单跳及普通题Overall均为官方口径 **Answer F1（%）**；Overall按全部1,540道普通题汇总，不是四类别等权平均，也不含对抗题。

| 方法 | 多跳<br>282题 | 时序<br>321题 | 开放域<br>96题 | 单跳<br>841题 | 普通题Overall<br>1,540题 | 对抗拒答分†<br>446题 | 已知Token/题‡ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Memoria-Agent：RRF + 邻句，首次回答 | 43.44 | 52.12 | 19.55 | 66.08 | 56.12 | 94.17 | 3986.38 |
| Memoria-Agent：RRF + 邻句 + 全历史复核 | 45.09 | 55.95 | 22.47 | 68.37 | 58.65 | 92.38 | 18414.96 |
| Memoria-Agent：RRF + 定向补检 | 44.30 | 56.29 | 21.20 | 66.48 | 57.47 | 92.83 | 7290.80 |
| Memoria-Agent：充分性 + 风险核验 + 自适应补检 | 37.45 | 46.49 | 11.07 | 62.10 | 51.15 | 95.96 | 12663.62 |

† 对抗分为官方拒答短语分，**不是F1、语义准确率或结构化拒答率**。最终结构化拒答：全历史410/446，定向413/446，风险自适应428/446。前两行是原同批首次/最终阶段，后两行是新的独立run；同冻结题目、Reader配置与本地Embedding快照，预算和额外调用不同，尚无同协议外部方法对照。

‡ Token/题以全部1,986题为分母，包括所有Reader、缺项规划、前后核验及工程重试的已知usage，**是下界，不是账单**。原账本还存在未知usage/在途费用，新定向和自适应分别有1/7次未知usage，均不计零成本。

原RRF首次证据包上限12,000字符，拒答/非法输出触发最多一次完整同对话历史复核，完整请求上限240,000 UTF-8字节。新定向只补检一次；新自适应最多两轮，证据阶段12,000/16,000/20,000字符，每题每次执行最多6次模型调用、累计160,000完整输入字节，均不向Reader发送完整历史；远程精确Token窗口仍未知。

**P0综合目标尚未达到。** 定向较全历史节省约60.41%已知Token，但普通F1下降1.18个百分点；风险自适应节省约31.23%，对抗短语分提高3.59个百分点，普通F1下降7.50个百分点。普通拒答数为166→201→400，回答零F1数为100→90→55；不能据此宣称幻觉减少，独立语义审核仍未做。新定向最终执行错误0，自适应有1题核验输出达到长度上限，保留全部分母并记失败；历史失败与恢复费用均保留。

生产默认保持，`use_answer_risk`仅显式开启前后核验，完整生产补检尚未接入。实现、预算/引用审计、门控代理、失败及下一步校准见[P0报告](docs/implemented/证据充分性与风险感知回答.md)；原两阶段结果见[历史全量报告](docs/implemented/LoCoMo全量评测与英文检索修复.md)。这些是官方构建的文本benchmark结果，真实本人长期效果和抽取F1仍待新授权连续资料验证。

<details>
<summary>查看四方法质量、拒答与成本图及复现来源</summary>

![LoCoMo四方法全量消融](docs/assets/evaluation/locomo-risk-full-20261014.png)

[完整聚合](docs/assets/evaluation/locomo-risk-full-20261014.json) · [SVG](docs/assets/evaluation/locomo-risk-full-20261014.svg) · [复现与指纹](docs/assets/evaluation/README.md#p0四方法全量消融2026-10-14) · [原两阶段分类图](docs/assets/evaluation/locomo-full-20261013.png)。绘图0新增模型调用、0新评分。

</details>

## 首次准备环境

以下为 **Windows PowerShell**，除注明外从仓库根执行，无需激活.venv。本机路径为`C:\Users\zero\Desktop\Memory-Project\Memoria-Agent`，新机器先获取仓库：

```powershell
# 已有项目不重复clone
git clone https://github.com/ZJ331456/Memoria-Agent.git
Set-Location Memoria-Agent
# 本机可直接 Set-Location 'C:\Users\zero\Desktop\Memory-Project\Memoria-Agent'

if (-not (Test-Path .venv\Scripts\python.exe)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# 保留已有私密配置
if (-not (Test-Path config.toml)) { Copy-Item config.example.toml config.toml }
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable); print('isolated:', sys.prefix != sys.base_prefix)"
```

本机已验证Python3.13.5、Node22.16.0、npm10.9.2。API最小依赖在requirements.txt；本地模型可选依赖在requirements-local-embedding.txt，向量扩展在requirements-vector.txt。Node用于Studio/MCP。这是当前平台证据，不代表全部系统已验收；不向conda base或全局npm安装项目包。

## 模型方案：选择一种

准备`model/bge-small-zh-v1.5/`，使用本地LLM时另备`model/Qwen3.5-2B/`。模型被Git忽略，clone不会包含权重；本项目没有通用Qwen下载CLI，需有效的本地Safetensors/tokenizer/config。

```powershell
# 本机RTX4060已验证CUDA wheel；CPU机器选择官方CPU wheel
.\.venv\Scripts\python.exe -m pip install torch==2.9.0 --index-url https://download.pytorch.org/whl/cu126
.\.venv\Scripts\python.exe -m pip install -r requirements-local-embedding.txt
.\.venv\Scripts\python.exe scripts/download_embedding.py
```

已有环境/模型时跳过；BGE下载固定revision并核对SHA/大小。驱动与wheel按[Embedding说明](docs/implemented/本地Embedding配置.md)和[PyTorch官方安装表](https://pytorch.org/get-started/previous-versions/)核对。

### A. 远程LLM + 进程内本地BGE

在私密config.toml配置main/fast与embedding；模型名称使用账号可调用的ID，下例为本机历史评测使用的DeepSeek名称。

```toml
[llm.main]
model = "deepseek-flash"
base_url = "https://api.deepseek.com/v1"
api_key = "${DEEPSEEK_API_KEY}"
[llm.fast]
model = "deepseek-flash"
base_url = "https://api.deepseek.com/v1"
api_key = "${DEEPSEEK_API_KEY}"
[memory.embedding]
model = "model/bge-small-zh-v1.5"
base_url = "local://auto"
api_key = ""
```

新机器用环境变量时，在**启动业务服务的同一终端**隐藏输入，不打印Key：

```powershell
$taskSecureKey = Read-Host 'DeepSeek API Key' -AsSecureString
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new('', $taskSecureKey).Password
Remove-Variable taskSecureKey
```

本机qin已有账号绑定DPAPI profile，无需重输或覆盖。`data/users/<UUID>/config/models.override.toml`会覆盖槽位，已有覆盖时只编辑config.toml可能不改变实际配置。`configure_research_model.py`仅保存研究profile，单独执行不激活生产；见[脚本说明](scripts/README.md#研究与模型配置)与[统一接口](docs/implemented/本地与远程OpenAI统一接口.md)。

### B. 本地Qwen/BGE共用HTTP模型服务

在**终端一**启动8080，默认config.example.toml对应此方案：

```powershell
.\.venv\Scripts\python.exe scripts/serve_local_llm.py --model model/Qwen3.5-2B --embedding-model model/bge-small-zh-v1.5 --device cuda --host 127.0.0.1 --port 8080 --max-context-tokens 8192 --warmup
# 另一个终端只读确认
Invoke-RestMethod http://127.0.0.1:8080/health
Invoke-RestMethod http://127.0.0.1:8080/v1/models
```

设备可选cpu/auto；本机验证为CUDA。服务提供chat/completions与embeddings，Qwen总输入+输出默认8192 token、BGE单块512 token；health存活不是模型质量证明。warmup加载并运行本地LLM，长文分块由记忆链路执行，直接超长embedding不会静默截断。[完整接口边界](docs/implemented/本地与远程OpenAI统一接口.md)。

## 构建Studio、启动2237业务

先构建，再在**终端二**启动；方案A无需另起8080。

```powershell
Push-Location studio
npm ci --cache .npm-cache
npm run build
Pop-Location
.\.venv\Scripts\python.exe main.py --config config.toml --host 127.0.0.1 --port 2237
```

| 地址 | 用途 |
| --- | --- |
| http://127.0.0.1:2237/studio/ | Memory Studio登录与记忆管理 |
| http://127.0.0.1:2237/docs | Swagger接口契约 |
| http://127.0.0.1:2237/api/health | 业务进程存活，匿名可读 |
| http://127.0.0.1:2237/ | JSON导航 |
| http://127.0.0.1:8080/v1/models | 方案B独立模型服务 |

```powershell
Invoke-RestMethod http://127.0.0.1:2237/api/health
```

Studio503时先构建再重启；端口占用先核对已有服务，不重复起两份。修改模型override后重启业务。当前按单个业务进程运行，多worker租户生命周期、任务和MCP协调尚未验收。

### 首次账号与登录

Studio有登录，没有注册页面；新机器可本机隐藏输入建立自己的账号：

```powershell
.\.venv\Scripts\python.exe scripts/manage_users.py reserve your_username
.\.venv\Scripts\python.exe scripts/manage_users.py set-password your_username
```

用户名3–32位小写英文/数字/下划线，以字母开头，密码12–128字符；也可通过/api/auth/register注册。本机qin已配置，正常登录即可，重设会撤销会话。正式业务始终要求Cookie/Bearer登录，api_token空不免登录。Studio不保存明文密码或Bearer，见[用户隔离](docs/implemented/用户登录与数据隔离.md)。

### 前端开发模式

API保持运行，在第三个终端：

```powershell
Push-Location studio
npm ci --cache .npm-cache
npm run dev
```

访问http://127.0.0.1:5173/studio/，/api代理到2237；部署模式只需构建产物和2237，不需要Vite开发进程。

## 可选MCP与公开诊断来源

这些不是最小启动必需项，仅配置已有账号：

```powershell
.\.venv\Scripts\python.exe scripts/install_mcp.py
.\.venv\Scripts\python.exe scripts/configure_mcp.py --user your_username
.\.venv\Scripts\python.exe scripts/check_mcp.py --user your_username
# 飞书首次私密输入并授权；重授权可加 --login-only
.\.venv\Scripts\python.exe scripts/configure_feishu.py --user your_username
```

依赖在.mcp，凭据按账号隔离；飞书回调http://localhost:3000/callback。安装、握手、授权、业务调用分别核验，详情见[MCP文档](docs/implemented/MCP部署与授权.md)。

```powershell
# 可选公开来源；已有qin语料无需重导
.\.venv\Scripts\python.exe scripts/prepare_real_dialogues.py --limit 50 --user your_username
```

50段/206原话来自LCCC，进入账号实验空间；身份/事件时间未知，不生成本人Claim。旧个人历史仍不导入，来源/许可见[datasets](datasets/README.md)。

## 实际数据存储

```text
data/                              # 全部忽略
├─ benchmarks/locomo/raw/          # 本地官方benchmark副本
├─ auth/accounts.db                # 账号、密码摘要、会话摘要、登录尝试
├─ users/<server UUID>/
│  ├─ memory/memoria.db             # 原话/记忆/Claim/审核/视图/索引/任务
│  ├─ markdown/                    # 记忆投影与人工维护内容
│  ├─ config/                      # 账号模型覆盖/MCP
│  ├─ secrets/                     # DPAPI profile/OAuth，不写日志
│  ├─ research/                    # 来源、标注、inputs/gold分离
│  ├─ experiments/<run_id>/         # 私有执行与原始产物
│  └─ reports/                     # 账号报告
└─ legacy/                         # 旧档案封存，未导入
model/                             # 权重/tokenizer，忽略
```

当前每用户SQLite/WAL+本地文件。完整备份/恢复、账号注销/全量导出、备份删除政策、schema版本迁移及跨机凭据迁移尚缺；普通Markdown导出不是完整备份，NTFS/DPAPI也不是整库加密。[商用存储验收](docs/plans/长期陪伴记忆研究与实验计划书.md#18-下一阶段功能优先级与商用准备)。

仓库中的[datasets/](datasets/README.md)保存来源、许可与SHA清单；原始benchmark、私有输入/gold和逐题结果放在被忽略的data/，公开结果汇总与图表放在[docs/assets/evaluation/](docs/assets/evaluation/README.md)。

## 下一步重点与商用缺口

1. **回答风险与误拒答校准：** 已完成充分性/风险核验与定向/自适应全量消融，综合目标未达；下一步校准缺项/冲突状态一致性并预留完整Reader+支持预算，保留原文约束、生产审批和默认策略。
2. **多跳、开放域与时间推断：** 改善跨片段证据覆盖、指代和时间精度。标注证据ID可见不等于文本足够；需区分检索、证据选择与Reader问题。
3. **小模型的同预算验证：** 使用可信本地Reader tokenizer做紧凑证据与需求预测对照，另登记同模型/预算/评分的基线及独立留出。完整历史回退结果不能直接推广到1B模型。
4. **真实长期研究：** 获得新授权的连续本人资料后，再测Claim抽取、条件、更新决策及长期保持/变化。独立真人研究评估暂缓，当前公开语料不补造成本人历史。

当前存储为每用户SQLite/WAL与本地文件。商用还缺备份核验/恢复演练、schema迁移、账号全量导出/注销及备份删除政策；同时需资源/成本配额、SSRF出口治理、HTTPS可信代理、租户回收、readiness/监控/告警、CI与依赖锁定、发布回滚和许可清单。具体阶段与验收见[算法下一阶段](docs/plans/长期陪伴记忆研究与实验计划书.md#231-全量完成与接下来验收)及[商用准备](docs/plans/长期陪伴记忆研究与实验计划书.md#18-下一阶段功能优先级与商用准备)。

模块职责、工程验证与完整阶段记录统一从[文档导航](docs/README.md)查看；AI协作与提交遵循[AGENTS.md](AGENTS.md)及[GitHub规范](docs/implemented/github提交规范.md)。私密配置、原话、逐题结果和模型不进入Git，旧个人历史仍不导入。
