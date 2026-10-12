# 已实现：Memory Studio 与统一记忆访问

核对：2026-10-12。本轮按用户明确授权交付专用记忆工作室：来源审核、变化查看、访问诊断和只读运行账本。旧聊天前端仍移除；没有新增演示画像、模拟研究数据或自动批准。

## 启动与使用

本机已在studio安装依赖并构建。源码更新后先构建、再重启Python服务：

```powershell
Push-Location studio
npm ci
npm run build
Pop-Location
.\.venv\Scripts\python.exe main.py --host 127.0.0.1 --port 2237
```

打开 `http://127.0.0.1:2237/studio/`，用已有账号登录；业务数据仍走原有登录网关。未构建时该路径返回503及构建说明，根路径仍是JSON导航。开发模式在studio执行npm run dev，Vite代理/api到2237。

依赖和npm缓存只在studio，Python仍使用.venv，没有修改conda base或全局npm。实际锁定Vue 3.5.43、TypeScript 5.8.3、Element Plus 2.14.7、Vite 6.4.4；选择兼容本机Node 22.16的Vite 6，不自动升级全局Node。工具链参考[Vue官方文档](https://vuejs.org/guide/quick-start.html)与[Vite文档](https://vite.dev/guide/)。dist/node_modules不提交。

| 页面 | 真实操作 | 边界 |
| --- | --- | --- |
| 记忆浏览与审核 | 最多500条Claim、状态筛选、来源范围/高亮、条件/极性/模态、不可变历史与值差异；提出候选、显式抽取、批准/拒绝/争议/撤回/恢复、修订候选 | 原话必须具备个人Claim资格；审核要原因和expected_revision；没有预置画像 |
| 双时间与变化 | 中文日/月/年区间解析，事实/获知时间独立输入，修订时间线 | 区间不是某一天；pending和未知事实开始保留，不能推断整年状态 |
| 检索实验室 | Unified/Claim/Keyword/BM25/Dense/RRF，固定候选内topk/coverage，字符预算，完整证据，过滤/缺项/通道排名/实际耗时 | 不调用reader；纯来源通道不暗中混入Claim；补检不晋升个人状态 |
| 评测运行记录 | 本账号已有manifest、代码/数据SHA、角色参数、指纹核验后的执行metrics | GET只读，不启动脚本、不下载gold/prompt；独立评分修订及逐题失败分析未接入 |

会话使用HttpOnly、SameSite=Strict Cookie，用户名/密码只提交本机登录接口，不写localStorage/sessionStorage。退出或401会中止请求，清空来源/候选/查询/账本/时间输入，过期响应不能进入新账号界面。Unicode span使用Array.from码点定位emoji，并核对当前来源SHA；Vue转义显示原话，没有v-html。来源读取失败清空旧展示；参数变化清空旧结果，调用期间改参数或切页不显示旧请求结果。Studio有CSP/no-store/nosniff；同源写请求通过，外部Origin和cross-site写仍拒绝。

## 统一访问链路

`POST /api/memory-query`：保守中文解析 → 已批准Claim/有效视图或时间线 → 缺项时普通记忆RRF来源补检 → 重新核对父版本/事实有效区间/来源角色/撤销/公开归属 → 去重及整组预算 → 路径/证据/解释。身份只来自服务器登录UUID，拒绝user_id等未知字段。

请求字段query、subject_id、predicates、context、fact_as_of、recorded_as_of、mode、selector、budget_chars、top_k、candidate_limit。默认unified/coverage/6000字符/8证据组/32检索结果；最多64候选、32证据组、256–16000字符。调试候选不计入selection预算；完整调试响应不是reader上下文。

Unified先读Claim，完整状态省掉补检；条件缺失/冲突保留clarify。Claim只读结构化状态；Keyword/BM25/Dense/RRF是独立来源通道，关闭图扩展。Dense不可用明确dense_available=false，不伪装成词面结果；RRF沿用生产权重0.8/1.0/0.9与k=60，报告通道rank/相似度/扫描限制/实际降级。空候选不调用embedding。

来源补检仅接受仍存在、未撤销的本账号用户原话，公开导入message不能用于个人Claim。普通记忆片段标unverified_retrieval_hint，正文来自当前原话，关联和语义支持未独立核验。没有获知时间索引，因此recorded_as_of跳过普通语义补检，不用之后资料回答“当时知道什么”。异步检索返回后重查Claim、父正文/更新时间与事实有效区间，变化候选被过滤。

AgentRuntime对短而明确的个人状态问题使用统一入口，跳过旧摘要规划和二次普通召回；一般任务保留既有planner。reader只收到selection、路由/缺项/限制，不收到完整调试候选池；Structured State原子省略，trace记候选/选中ID、过滤及阶段耗时。原有/api/memory-access与access_claims契约保留。

## 时间解析与预算适用范围

确定性规则覆盖12槽位常见词、明确“面试模拟”activity=interview及有限中文日历表达。参考时间UTC+8；去年/前年/今年、上个月/这个月、昨天/前天与1900–2099年范围内的年/月/日返回半开区间。多时间、无效日期、“最近/当时/上周”、未知第三方须澄清；不猜别名。自然条件与显式context矛盾也澄清，精确时间不绕过主体/条件冲突。

区间查询展示可核对来源的不可变修订，包含明确标记pending；未知fact_start不变成区间内事实，不证明状态持续整年。“上个月你认为……”需要明确获知截止点，不偷选月末；recorded_as_of过滤之后修订并隐藏未来退休时间。事件日期没有可靠来源时仍未知。纯来源通道的日历区间请求需明确fact_as_of，不硬转为某日。

选择器只用查询槽位requirements和候选分数，不读gold。topk按分数稳定排序；coverage按新增槽位覆盖、分数、固定候选序号贪心选择。一个槽位的条件/正负约束/适用Claim为不可拆组；来源候选按message ID去重。成本为完整selection JSON的Unicode字符数，**不是精确reader token**。一般事件/参与者需求学习、MMR、精确token预算及真实B7效果仍待实现/评测，不是通用最优算法。

## 候选审核辅助

`GET /api/claim-source/{message_id}`：本账号来源、精确SHA/span及personal_claim_eligible；公开来源可浏览但不能作为个人Claim。抽取新增显式infer_conditions=true，仅支持原话明确面试模拟条件；condition_quote唯一匹配、包含在evidence_quote并含“面试模拟”，不能改调用方activity、丢条件或补时间。结构/证据失败整批回滚，结果仍pending；其他条件未完成。

`POST /api/claims/{id}/analyze`：expected_revision、use_model（默认false）。默认给值首尾空白建议及同主体/槽位/typed scope的create/reinforce/review建议；强化需值/极性/模态/事实边界/父记忆一致，变化/并存仍明确审核，不静默替代。

显式use_model=true调用已配置LLM读取引用，建议Supported/Contradicted/Insufficient。semantic_support_verified=false、persisted=false、automatic_approvals=0，不属于真人或现实核验。返回后重查版本/来源指纹，变化409作废；失败502且不写Claim。同模型不能作为独立裁判，本轮没有为新NLI功能发起真实远程质量实验。

## 私有运行记录与验证

`GET /api/research-runs`、`GET /api/research-runs/{run_id}`只读取服务器绑定账号experiments目录的规范run ID。拒绝遍历、symlink/junction、归属不符、超1MiB JSON、metrics SHA/大小不符。白名单不含endpoint、凭据、prompt、原话或gold；人工/语义/偏好指标不从执行数字推断，保持未知。原始记录/评分不改写。

2026-10-12首轮相关86 passed / 24.58 s；新增边界后全量393 passed / 111.31 s，新增只读账本后全量394 passed / 88.48 s。前端3项测试通过（码点、跨账号延迟响应、401）；vue-tsc及生产构建通过，JS321.09kB/gzip113.58kB、构建4.74 s，不是线上延迟。浏览器临时独立账号实测登录、空列表、年份区间、Keyword空召回、只读空账本、退出清理，没有导入生产或作为研究样本。最终回归证据另见本节末尾。

实际qin只读核验：50会话/206消息/0 memories/0 Claim，尚未打开登录租户故Claim表未初始化；FK错误0、integrity=ok。39份已有manifest可读、39份metrics的SHA/大小匹配。真实health/Studio/OpenAPI均200、匿名memory-query/research-runs均401；本轮核验生产记忆写入0、模型调用0、旧个人归档未读。

以上证明工程链路和界面可用，不证明通用条件F1、B3自主写入、B5/B6正向收益、B7质量提升或真实“越来越熟悉”。下一步见[研究计划](../plans/长期陪伴记忆研究与实验计划书.md#17-memory-studio与算法闭环迭代)。

2026-10-12最终代码全量工程回归 **405 passed / 91.20 s**，前端3项测试通过、vue-tsc与生产构建通过；临时浏览器真实HTTP链路与实际qin只读核验均通过。工程质量保护不是长期研究收益，真实连续人物/正向视图/B7效用仍未测。
