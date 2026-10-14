# 已实现：Claim、条件视图与访问路由

核对：2026-10-12。实现为账号内的结构化记忆服务，入口是登录后的 HTTP API、`access_claims` 只读工具及有限的中文状态问题。工程验证不等于长期陪伴效果；本机现有 LCCC 语料仍不构成个人 Claim。

## 使用流程

原话已在当前账号的会话中保存 → 提交结构化候选或显式调用抽取 → 核对值、主体、条件和原话 → 明确批准 → 按槽位/条件访问。首次完整当前读取会编译确定性视图，后续相同请求优先复用；变化后重新查 Claim，而不是使用旧视图。Swagger `/docs` 提供所有接口。

| 接口 | 行为 |
| --- | --- |
| `GET /api/claim-slots` | 可用槽位、单值/多值和条件规则 |
| `POST /api/claims` | 提交候选，201；初始状态 pending |
| `POST /api/claims/extract` | 从一条本账号原话生成最多3个待审核候选；不会自动批准 |
| `GET /api/claims`、`GET /api/claims/{id}` | 当前候选/版本；列表默认100、最多500 |
| `GET /api/claims/{id}/history` | 不可变修订、事实边界、系统获知/退休时间和来源状态 |
| `POST /api/claims/{id}/revisions` | correct / reinforce / transition；新修订仍 pending |
| `POST /api/claims/{id}/approve` | 原子批准；需要 expected_revision、reason |
| `POST /api/claims/{id}/reject` | 拒绝候选；旧批准状态继续有效 |
| `POST /api/claims/{id}/retract`、`dispute`、`restore` | 撤回、争议或明确恢复有效旧版本；同样检查当前版本 |
| `DELETE /api/claims/{id}` | 永久删除根及修订/来源关联，清除依赖缓存内容 |
| `POST /api/claim-views`、`GET /api/claim-views/{id}` | 编译/检查当前条件视图；无效视图 payload=null |
| `POST /api/memory-access` | 返回 view / retrieve / history / clarify 及原话证据、原因和耗时 |

请求不接受 user_id、目录或模型端点；user_id 来自网关。跨用户 ID 返回404，匿名业务请求401；旧 Agent key 不能代替登录。完整登录过程见 [用户隔离](用户登录与数据隔离.md)。

## Claim 的数据及校验

根保存 claim_id、登录 user_id、subject_id、predicate、scope 和变化的前驱。`self` 表示当前账号显式提交的本人陈述；`entity:<去标识化ID>` 是调用方明确提供的主体，工具不推断真人身份、不做实体别名合并。根的主体/槽位/条件不可原地更改；改条件须提交独立候选。

修订保存 value（目前为字符串）、polarity、modality、证据、事实区间/精度、observed_at、recorded_at、状态、提交/批准账号、原因及可选普通 memory_id 和模型 revision。数据库禁止 UPDATE 修订；新增版本保留旧记录，永久删除除外。`proposal_origin=model` 标识模型候选，pending 的 reviewer=null；`user_approved` 表示账号内明确批准，不代表独立真人审核。

槽位：residence、occupation、school、response_language、response_detail、diet、work_habit、hobby、learning_goal、project_goal、time_preference、budget。residence、response_language、response_detail、budget 为单值，其余多值。槽位名称固定；字符串值不做通用语义规范化或自动同义合并。

- 证据须为本账号仍存在的 user 消息，message_id、UTF-8 content_sha256、Python Unicode 字符索引 `[start,end)`、quote 均与原文匹配。助手发言不是本人证据。
- 已导入公开语料的消息不能成为个人 Claim 证据：数据库 role=user 只是原话容器，qin 归属不是原作者身份。没有把现有50段对话自动造为画像。
- 未知事实时间保持 null/unknown。提供边界须带时区、声明 day/exact 精度，结束必须晚于开始；不从导入时间补造事件时间。
- 关联 memory_id 可选，只能绑定本账号活动版本并保存正文 SHA；普通记忆更正/删除/失效后，当前 Claim 读取失败关闭，等待明确修订。Claim 不自动改写普通记忆。
- `semantic_support_verified=false`：原文精确定位不证明值或主体在语义上受支持，也不证明现实正确。

Claim 不镜像为普通 memories/Markdown/向量记录，防止“仅旅行时”的条件被自由文本召回丢掉。既有普通记忆不自动迁移；共享治理表也不复用为个人状态库。

## 抽取及批准

抽取接口接收 message_id、显式 subject_id 与 scope；输入单条原话1–12000字符。调用当前账号 main 模型，温度0、top_p1、输出最多2048 token，保存返回 usage 和实际墙钟；模型权重 revision 未知时保留 null。当前显式抽取接口不接入后台 memory_jobs，也不自动替代已有普通候选队列。

模型只提供槽位、值、极性、陈述/计划/推断及连续引用；程序从原文唯一匹配引用后计算 span/SHA，事件时间一律保留未知。整批先验证，再原子提交；格式错误、重复位置歧义、来源在等待模型期间被改写/删除/撤销，均不留下半批候选。公开来源在模型调用之前拒绝。仅显式调用该接口才发原话到配置的模型；可使用已适配的本地或远程统一接口。

批准时重新核验来源和版本，包括关联普通记忆正文的候选快照；正文改写返回409，不把新正文悄悄接到旧候选。同一来源不会增加独立支持次数，support_group_ids 以原消息去重。单值同条件、重叠事实区间的另一根须明确更正或变化，不靠相似度静默替代；多值同值/同极性的重复支持须强化已有 Claim。

下面为字段说明，尖括号须替换为实际原话和 ID，不是导入数据：

```json
{
  "subject_id": "self",
  "predicate": "response_language",
  "value": "<核对后的值>",
  "scope": {"travel": false},
  "evidence": [{
    "message_id": "<原话ID>",
    "start": 0,
    "end": 12,
    "quote": "<实际连续原文>",
    "content_sha256": "<整条原话的SHA256>"
  }]
}
```

批准使用刚读取的 revision，不假定固定版本号：`{"expected_revision": <当前revision>, "reason": "<实际核对理由>"}`。并发改变后返回409，重新读取后决定。

## 更正、变化和历史

`correct` 修正同一根的断言，需完整新值/证据/事实边界，批准前旧状态仍生效。`reinforce` 合并来源，不能改变值、极性、模态、时间或关联记忆。`transition` 表示真实状态变化，须提供新事实开始时刻：产生新根，批准事务同时追加旧根的结束边界和新根的批准版本，两者使用同一个 recorded_at；不原地改写旧断言。条件例外是独立 scope，不替代全局根。

事实区间为 `[fact_start,fact_end)`；recorded_as_of 选择该时刻已获知的最新非 pending 修订，再按 fact_as_of 筛选。批准状态不会被尚未批准的草稿提前退休。当前读取可保留明确声明但未知起始时间的状态；显式 fact_as_of 不把未知起点当成历史事实。纠正回答“当时我们知道什么”和变化回答“现在知道当时是什么”分别保留。

撤回/争议不再进入当前状态；restore 明确选择最近一个来源和关联版本仍有效的已批准修订，通过普通冲突检查再追加新版本，不复活已撤销来源，不扩展事实时间。普通 `undo-sources` 同事务登记 Claim 来源撤销并使视图失效，即使没有普通 memory_operations 也生效。来源删除/改写后，历史详情同样不从旧 JSON 或缓存恢复引用；历史返回 source_available=false、value=null。

此双时间查询仅适用于 Claim 修订；原有普通 memories 的时间 API 仍是有效区间查询。删除/隐私失效优先于历史回放，不能靠 recorded_as_of 读回已不可用的来源。

## 条件视图及依赖失效

编译器为 `deterministic-claims-v3`，直接序列化已批准字段和证据，不调用模型生成自由摘要。视图保存账号、请求主体/槽位/条件/scope_mode、预算、编译版本/时间、有效期、Claim revision_id 和每个请求槽位的 generation。空槽位也登记依赖，防止后续新增事实继续命中旧空视图。

条件最多8个，键为小写标识，值支持字符串/布尔/整数；按类型一致的等值合取匹配，true不等于1。不是任意表达式执行器。条件缺项时返回 clarify，不猜是否旅行或工作。单值采用更具体的适用条件；多值按每个值选择更具体的适用极性，保留其他全局值，避免旅行爱好抹掉全局饮食限制。相同具体程度的冲突保留未知；只有否定约束不能给出未知的正向事实。inference不作为当前状态，居住等事实的plan不当已发生；学习/项目目标允许明确标注的计划。

```json
{
  "predicates": ["response_language", "response_detail"],
  "context": {"travel": false},
  "budget_chars": 6000
}
```

当前完整查询第一次走 retrieve 并自动编译，下一次相同请求可走 view；也可主动 POST claim-views，增加 ttl_seconds（1–86400，默认600）。每账号最多128个缓存视图，超出淘汰最旧项。预算256–16000个序列化字符，不是精确 tokenizer 预算；整个槽位组放入或省略，不能截半条条件/引用。

批准、更正、变化、撤回、恢复、删除，以及关联消息/记忆变化在同一数据库事务中更新对应主体/槽位 generation，清空依赖视图 payload；不使无关槽位的视图全部失效。SQL触发器覆盖通过旧记忆接口发生的变化。TTL、已知未来开始/结束时刻、编译版本、依赖版本和当前来源在读取时再次校验，无须等待定时器；跨连接写事务防止先读旧版本再发布新缓存。

视图失效后重编译该请求的全部槽位，不是局部补丁式增量编译。generation 是 Claim 状态水位，不是生产 Dense 索引 watermark；父记忆 embedding 更新也会保守地失效关联视图。详情、列表、历史、视图和访问读取都持有一致事务快照，避免跨连接在来源校验与取引用之间改写原文。

## 四条访问路径与对话集成

| path | 当前行为 |
| --- | --- |
| view | 相同主体/槽位/条件/预算的有效完整视图 |
| retrieve | 冷缓存或失效后按结构化槽位索引读取；完整时编译新视图 |
| history | 显式 history / fact_as_of / recorded_as_of，绕过当前缓存并返回对应修订的原话证据 |
| clarify | 缺来源、条件、正向状态、冲突或预算不足；可保留不冲突的部分状态，但不冒充完整答案 |

返回原因、missing、complete、证据span、view_id/compiled_view_id、字符预算与实际 elapsed_ms。这里的 retrieve 是 Claim 槽位查找，尚不调用 BGE Dense/RRF；历史路径是 Claim 证据回查，不是任意共同经历检索。

运行时明确识别“我现在住在哪里？”、“我的回答语言偏好？”、“我的回答详细度偏好？”及代码列出的少量同义问法：跳过摘要编译、模型查询规划和普通语义召回，注入结构化状态，trace 记录 claim.access 阶段、路径/证据。任意自然语言、复杂条件和一般任务仍走原有路径，可由 `access_claims` 指定槽位/条件/时间；不承诺已理解所有个人问题。

结构化提示段和工具结果在后续整帧、工具/整轮预算中必须完整保留或明确省略，不能裁掉条件只剩值。最终 reader 仍是模型，路由与提示正确不等于回答一定遵守证据；语义 QA 和真实陪伴改善仍需后续评测。

## 验证与本机范围

2026-10-12算法深化：scope_mode=default仅查无条件状态且不与context混用；applicable保留严格缺项澄清。统一入口按明确默认/面试解析，精确时间不覆盖冲突。[独立核验与影子演化](Claim证据核验与演化策略.md)接入analyze/extract，数据库批准规则保持。

工程测试仅用临时输入：槽位/来源/时间校验、待审不生效、纠正/变化双时间、未来边界、条件例外、多值约束、同来源去重、撤回/恢复、缺项依赖、无关槽位不失效、并发批准、注入故障回滚、源删除/改写、父版本失效、账号隔离、真实网关契约、运行时/工具与原子预算。不会导入产品或当研究分数。

2026-10-12：全量工程回归初次351 passed / 124.98 s；补充初始化事务、历史退休边界和缓存上限后，Claim/账号隔离/预算定向73 passed / 19.27 s。补充多值条件覆盖后全量355 passed / 80.75 s；最后增加批准时父正文快照检查及跨连接读取竞态，定向60 passed / 15.58 s，最终全量359 passed / 84.08 s。测试耗时不代表开发工时或线上访问延迟。

qin真实库的一致性副本迁移/重启检查通过：50会话、206原话、0普通记忆、0 Claim，外键错误0、integrity=ok；真实公开来源提交及抽取均在模型调用前拒绝，实际模型调用0。检查墙钟149.496 ms是迁移/隐私检查时间，不是生产访问性能。该检查没有修改生产库；回执和副本位于忽略的data/reports/system。

尚未测：新槽位抽取的真实语义支持度、本地小模型写入/回答质量、真实连续人物变化、Claim/视图对比生产RRF的质量/访问收益和B5/B6消融。没有把现有50段公开语料拼成用户历史或从工程输入造科研成绩。

实现：[claims.py](../../memoria/claims.py)、[HTTP路由](../../memoria/api/claims.py)；剩余任务：[研究计划](../plans/长期陪伴记忆研究与实验计划书.md)。

## 本地服务核验与启用

本轮真实启动 `main.py --host 127.0.0.1 --port 2237`：health / OpenAPI 为200；匿名 Claim 列表和 memory-access 均401；新路由存在。请求直连回环地址，不经过环境代理。未使用密码、未调用模型、未写个人事实，回执在忽略的data/reports/system。

已有账号在首次登录并创建运行时时自动初始化新表/触发器；本轮生产库仅做一致性副本迁移验证，未提前批量迁移，也未导入旧个人历史。管理员部署前应保留SQLite一致性备份。当前真实来源没有可批准的本人Claim，因此“状态缺失→clarify”是正确行为；不能为展示视图命中而将公开对话填成qin画像。

## 自动性能评测入口

新增实际账号一致性副本benchmark与来源前缀Dense/RRF读取实验，见[说明](DenseRRF自动评测与视图性能.md)。当前0已批准Claim，副本benchmark只测空状态与显式空缓存开销，完整画像收益未知。来源Dense/RRF评测未改变本接口的槽位读取语义，人工语义/陪伴偏好评估暂缓；生产批准仍明确执行。

独立阶段真实空状态基准5400次已完成：0 Claim、全部澄清、payload等价、生产写入0。显式空缓存增加访问开销，不能据此断言完整画像的视图收益或失败；真实连续资料及已批准个人状态仍待提供。来源Dense/RRF自动21轮结果见[报告](DenseRRF自动评测与视图性能.md)，未将其接线改报为本接口完整生产补检。

## 本轮统一入口与工作室

已有/api/memory-access仍保持本篇限定契约。新增/api/memory-query编排Claim/视图、有限中文区间和普通记忆来源RRF补检，运行时有限个人问题改用统一入口；只向reader注入selection，不注入完整调试候选。抽取增加显式infer_conditions开关，仅支持来源约束的面试模拟条件；analyze只给更新/支持度建议，不自动批准，不修改既有语义核验字段。[工作室、契约与实际验证](MemoryStudio与统一记忆访问.md)。旧样本/gold不变，通用条件与真实长期质量仍未测。
