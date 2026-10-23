# Scripts：本机管理员与研究入口

[evaluate_llm_rerank.py](evaluate_llm_rerank.py)默认0调用验证；执行须`--execute --allow-remote --max-calls 12`，只测预声明四题、固定资料库候选与字符预算。保留真实Token/延迟及负向结果，[完整说明](../docs/implemented/LLM候选重排与同预算诊断.md)。

[根启动教程](../README.md) · [研究计划](../docs/plans/长期陪伴记忆研究与实验计划书.md)

以下命令从仓库根运行，解释器统一为`.\.venv\Scripts\python.exe`。这里是本机管理工具；Web接口不接受任意脚本、文件路径或他人研究输入。--help只显示参数，不代表模型调用成功。

[evaluate_entity_retrieval.py](evaluate_entity_retrieval.py)：`--user YOUR_USER`仅验证；加`--execute`执行本地BGE三方法对照，Reader另需`--reader-cases alias complement --allow-remote --max-calls 6`。虚构直接录入夹具、后验ID诊断、同字符预算及本机HTML详见[实体对照协议](../docs/implemented/实体身份与实体召回.md#第三阶段固定预算的实体对照)，不修改生产、不冒称真实语义质量。

## 一键业务后台

Windows入口集中在[cmd](cmd/README.md)：双击[start_backend.cmd](cmd/start_backend.cmd)启动，[stop_backend.cmd](cmd/stop_backend.cmd)关闭；从仓库根执行`.\scripts\cmd\start_backend.cmd -Action Status`查看状态。关闭时先校验根进程归属，再结束业务及其MCP子进程树。内部[backend.ps1](backend.ps1)仅用项目venv、私密配置、loopback和隐藏窗口，日志在忽略的data/logs/system，端口占用不另起/误杀。首次依赖及Chat/Studio统一构建先准备（[build_frontend.cmd](cmd/build_frontend.cmd)，内部[frontend.ps1](frontend.ps1)），本地HTTP模型8080仍独立启动。[完整使用及真实验证](../docs/implemented/本机后台启动与停止.md)。

Python依赖只维护根[requirements.txt](../requirements.txt)，按七类注释，安装命令包含全部类别。通用Python工具和Windows PowerShell实现保留在scripts；[sh](sh/README.md)只预留Linux入口位置，尚无Linux启动实现。

## 环境与模型

可选的[DBeaver数据库查看](../docs/implemented/DBeaver可选数据库查看.md)：`python scripts/configure_dbeaver.py --user your_username`仅预览；关闭DBeaver后加`--apply`，保存账号库、所选用户库和已有渠道库的只读连接及三份SQL。配置留DBeaver工作区，不加入业务启动或依赖；脚本不创建业务库，不调用模型。

| 文件 | 职责/命令 | 边界 |
| --- | --- | --- |
| configure_bot.py | `--channel feishu/qq/telegram` | 隐藏输入并DPAPI保存，统一bot.toml；不打印凭据 |
| check_bots.py | `--channel qq`（省略则检查已启用） | 只读鉴权/接入点检查，0消息/0LLM |
| configure_models.py | `show [--user your_username]`或`test --slot embedding` | show只读配置/0推理，remote test需--allow-remote；不打印Key |
| migrate_config_layout.py | 停服后`--config config.toml` | 合并系统/模型/飞书/MCP到config四文件；先备份，失败回滚，账号凭据仍隔离 |
| download_embedding.py | `scripts/download_embedding.py` | 固定公开BGE revision，核对SHA/大小，仅下载模型数据到model |
| serve_local_llm.py | `scripts/serve_local_llm.py --model model/Qwen3.5-2B --device cuda --port 8080 --warmup` | 本地文本chat与BGE embeddings；依赖/权重需先准备，进程存活不等于质量 |
| bench_llm_vram.py | 先用`--help`核对实际参数 | 本地模型显存诊断，需要模型；不能作为长期记忆成绩 |
| manage_users.py | `scripts/manage_users.py reserve your_username`及`set-password your_username` | 后者隐藏输入，重设撤销旧会话，不生成默认密码 |
| user_paths.py | 其他CLI复用账号目录解析 | UUID由账号库定位，不让用户自填另一个租户目录 |

## 研究与模型配置

```powershell
.\.venv\Scripts\python.exe scripts/research.py --user qin --help
.\.venv\Scripts\python.exe scripts/research.py --user qin run --help
.\.venv\Scripts\python.exe scripts/configure_research_model.py --user qin --help
```

research.py提供import-history/use-public、annotation-template/submit-annotation、freeze、run（extract/qa/retrieval）、score-template/score、recover-run、activate-model、benchmark-access。真实来源、同意范围、AI/真人来源与冻结要求见[研究工具](../docs/implemented/真实历史标注与评测.md)。import-history用于研究工作区，不等于已完成生产连续本人来源采集界面。

configure_research_model.py隐藏输入DeepSeek Key，保存账号绑定DPAPI profile，**单独执行不激活生产**。activate-model还需要已验证run及匹配profile；不要把研究配置命令描述成绕过验证的生产切换。账号专属模型在config/models.toml的accounts.<UUID>表覆盖默认槽位，详见[接口配置](../docs/implemented/本地与远程OpenAI统一接口.md)。

evaluate_retrieval.py读取公开注册协议、要求clean代码及指纹/分母一致，编排已冻结的真实调用；远程需显式allow-remote。旧evaluation_id不覆盖，重新评测建新登记，不把根README中的历史040427c结果算作当前代码成绩。

## MCP 与来源

| 文件 | 用途 |
| --- | --- |
| install_mcp.py | 无参数时在项目.mcp安装锁定搜索/飞书npm依赖和校验后的官方GitHub二进制；`--help`只显示说明，未知参数拒绝 |
| configure_mcp.py | `--user your_username`写账号独立工具白名单配置 |
| configure_feishu.py | `--user your_username`私密输入与OAuth；`--login-only`重授权 |
| configure_bot.py --channel feishu | 飞书入口；隐藏输入或`--from-user your_username`复用加密应用凭据，`--disable`关闭；无默认绑定 |
| mcp_server.py | 正式本机stdio桥接，读取指定账号凭据，避免命令行Secret |
| check_mcp.py | `--user your_username [--server search/github/feishu]`真实初始化与只读业务调用 |
| prepare_real_dialogues.py | `--limit 50 --user your_username`复现公开LCCC子集和回执，重复导入核对原文 |
| organize_local_data.ps1 | 整理既有本地目录，先阅读脚本与数据说明，不当成通用备份工具 |
| protect_local_data.ps1 | 本机NTFS权限保护；不是整库加密或跨机密钥迁移 |

MCP安装/初始化/授权/只读调用分别记录。[飞书回调及私密步骤](../docs/implemented/MCP部署与授权.md)。LCCC不变成本人Claim，旧个人历史仍排除。

## README 图表复现

```powershell
.\.venv\Scripts\python.exe scripts/render_project_diagrams.py
.\.venv\Scripts\python.exe scripts/render_project_diagrams.py --check
```

按所有者品牌包生成当前架构与记忆生命周期，不调用模型。旧LCCC与LoCoMo四方法绘图脚本已删除；历史代码可通过Git追溯。新双组评估只导出无原文/逐题答案的公开聚合，原始中间文件不推送。

## 同池证据选择

`research.py --user USERNAME select-evidence --source-run RUN_ID --budget 1024 --unit chars`复用本账号已有原检索池，四策略各写新run，0模型调用；`score --run NEW_RUN_ID`另读gold。`run --evidence-selector`可接新QA，远程需--allow-remote。源run/冻结协议/SHA不存在时拒绝，不读取旧个人归档。见[完整契约](../docs/implemented/条件时序抽取与预算证据选择.md)。

## LoCoMo十题测试

新增`locomo-smoke --input LOCAL_FILE`，固定官方SHA及10题，独立bundle/run，不导入生产或真实人物协议；远程仍需`--allow-remote`、账号私有profile和匹配roles。只验证执行链路，不自动扩全量或标语义正确。[本轮结果/英文分支限制](https://github.com/ZJ331456/Memoria/blob/05f9fb2ac08b0c8b37a359dd3e53b9cb4abedc7d/docs/implemented/LoCoMo十题真实调用诊断.md)。

## LoCoMo英文正式入口

新增`--fallback targeted|adaptive_risk`及`--scope development`（原顺序前两段全部304题），用于[P0充分性/风险消融](../docs/implemented/证据充分性与风险感知回答.md)。完整分母仍由`--scope full`保持；新模式不使用完整历史Reader，所有规划/核验及失败Token计入成本，独立注册协议与原始gold保持。旧`none|full_history`行为保留。

`research.py --user USERNAME locomo-run --help`；默认全1986题，`--scope smoke`同固定10题。英文BGE在model/，评分可选依赖`requirements.txt`安装到.venv；远程显式授权/profile/roles，默认并发4，错误不丢分母。旧locomo-smoke仅历史v1复现。[完整说明](../docs/implemented/LoCoMo全量评测与英文检索修复.md)。

`research.py --user USERNAME prepare-memory-probe --input PATH_TO_LOCOMO`仅准备SHA固定、单角色的生产记忆诊断输入，默认v2，`--protocol-version v1`保留描述性偏好首轮；不调用模型或导入生产库。随后`closed-loop --input PRIVATE_INPUT --execute --allow-remote --max-calls 8 --max-input-chars 60000`才真实执行原形成/审核/Claim/View/Reader及撤回；不跑官方全量QA。[完整命令与两轮实际结果](../docs/implemented/生产记忆闭环与逐题归因.md#claim与view真实调用补测2026-10-20)。

locomo-run新增--resume-from：必须先确认旧进程已停止并冻结为interrupted，由账号边界及产物/协议/模型指纹校验；不会对仍运行源run并发续跑。继承与新增调用分别记录，旧产物不覆盖。

`locomo-run --resume-from RUN --retry-errors`：仅对停止run的执行/回退错误恢复，完整分母继承，递归保留祖先调用/成本；未带resume-from则拒绝。

旧四方法图表与专用绘图器已退役；历史协议仅供研究追溯，不作为当前主入口。

本机未提交账号管理：manage_users.py新增set-role USER --role user|superadmin、disable-login USER。角色变更撤销会话，保留最后超管；停用只撤销登录，不删除个人数据。set-password继续隐藏输入12–128字符，无短密码例外。[权限边界](../docs/implemented/用户登录与数据隔离.md#本机qin账号与超级管理员2026-10-14仅本地未提交)。

## 项目说明与框架图

`render_project_diagrams.py`按品牌包离线绘制当前架构与记忆生命周期，导出PNG/SVG及来源指纹；`--check`验证资产和来源。旧ImageGen海报被替换，零新增图像或研究模型调用。

[config](../config/README.md)按system/models/bot/mcp各自统一，实际文件忽略、example公开，无local目录；冻结研究登记在eval/protocols。protect_local_data.ps1默认保护data及config，其他自定义私密路径需显式-Directory。详见[配置目录与模型后端](../docs/implemented/配置目录与模型后端.md)。

`test_memory_scenarios.py --user USER`默认0调用验证用户授权的[模拟场景](../simulation/README.md)；显式执行受总调用/字符上限约束，支持指定场景复测、仅进程直连、真实离线BGE和零调用累计清单。全部输出在忽略的simulation/runs，不写生产库、不作为研究成绩。

## 双组评估与零调用估算

research.py --user USERNAME eval-benchmark --dataset data/benchmarks/locomo/raw/locomo10.json --conversations 10 默认只估算，不调用模型。完整上下文Baseline与生产Kernel使用同模型、独立LLM二分类；实际执行另需execute、allow-remote、请求和费用上限，先看[成本与协议](../docs/implemented/双组内核评估与费用估算.md)。


正式顺序全量使用eval/protocols/kernel-vs-context-judge-v3-ordered-histories.json，参数--concurrency 1 --history-concurrency 8；先Memoria整组再Baseline，连续欠费停止新请求。publish_eval_summary.py --run PRIVATE_RUN --update-readme流式读取大检查点，只发布分类计数、阶段成本及失败汇总；不复制原文/逐题答案，前测单独计费。中间记录留`data/users/<UUID>/experiments`。

## 资料域实际BGE检查

`python scripts/check_library_retrieval.py --model model/bge-small-en-v1.5`在临时SQLite中用实际本地权重检查资料Keyword/BM25/Dense/RRF，0 API、不写生产账号。四路命中且Dense可用无退化才返回0；这是虚构功能验收，不是LoCoMo质量或替代完整模拟。
