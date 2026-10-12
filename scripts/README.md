# Scripts：本机管理员与研究入口

[根启动教程](../README.md) · [研究计划](../docs/plans/长期陪伴记忆研究与实验计划书.md)

以下命令从仓库根运行，解释器统一为`.\.venv\Scripts\python.exe`。这里是本机管理工具；Web接口不接受任意脚本、文件路径或他人研究输入。--help只显示参数，不代表模型调用成功。

## 环境与模型

| 文件 | 职责/命令 | 边界 |
| --- | --- | --- |
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

configure_research_model.py隐藏输入DeepSeek Key，保存账号绑定DPAPI profile，**单独执行不激活生产**。activate-model还需要已验证run及匹配profile；不要把研究配置命令描述成绕过验证的生产切换。现有账号models.override.toml会覆盖默认槽位，详见[接口配置](../docs/implemented/本地与远程OpenAI统一接口.md)。

evaluate_retrieval.py读取公开注册协议、要求clean代码及指纹/分母一致，编排已冻结的真实调用；远程需显式allow-remote。旧evaluation_id不覆盖，重新评测建新登记，不把根README中的历史040427c结果算作当前代码成绩。

## MCP 与来源

| 文件 | 用途 |
| --- | --- |
| install_mcp.py | 无参数时在项目.mcp安装锁定搜索/飞书npm依赖和校验后的官方GitHub二进制；`--help`只显示说明，未知参数拒绝 |
| configure_mcp.py | `--user your_username`写账号独立工具白名单配置 |
| configure_feishu.py | `--user your_username`私密输入与OAuth；`--login-only`重授权 |
| mcp_server.py | 正式本机stdio桥接，读取指定账号凭据，避免命令行Secret |
| check_mcp.py | `--user your_username [--server search/github/feishu]`真实初始化与只读业务调用 |
| prepare_real_dialogues.py | `--limit 50 --user your_username`复现公开LCCC子集和回执，重复导入核对原文 |
| organize_local_data.ps1 | 整理既有本地目录，先阅读脚本与数据说明，不当成通用备份工具 |
| protect_local_data.ps1 | 本机NTFS权限保护；不是整库加密或跨机密钥迁移 |

MCP安装/初始化/授权/只读调用分别记录。[飞书回调及私密步骤](../docs/implemented/MCP部署与授权.md)。LCCC不变成本人Claim，旧个人历史仍排除。

## README 图表复现

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-docs.txt
.\.venv\Scripts\python.exe scripts/render_eval_charts.py
.\.venv\Scripts\python.exe scripts/render_eval_charts.py --check
```

仅读取公开历史聚合表/注册协议/实现报告，生成PNG/SVG与SHA清单；不读取私有原话/gold，不调用模型或产生新评分。数据口径见[图表说明](../docs/assets/evaluation/README.md)。后台维护、备份恢复与完整注销仍是计划任务。

## 同池证据选择

`research.py --user USERNAME select-evidence --source-run RUN_ID --budget 1024 --unit chars`复用本账号已有原检索池，四策略各写新run，0模型调用；`score --run NEW_RUN_ID`另读gold。`run --evidence-selector`可接新QA，远程需--allow-remote。源run/冻结协议/SHA不存在时拒绝，不读取旧个人归档。见[完整契约](../docs/implemented/条件时序抽取与预算证据选择.md)。
