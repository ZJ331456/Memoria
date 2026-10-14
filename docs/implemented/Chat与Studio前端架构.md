# 独立Chat与Memory Studio前端架构

2026-10-14。按用户最新要求替代曾经重复后台布局的/app/工作台。Chat是独立对话产品界面，Studio是后台，两端面向所有登录账号。

## 路由与目录

| 入口 | 用途 | 源码 |
| --- | --- | --- |
| `/`，浏览器HTML请求 | Chat聊天、个人会话、流式答复与取消 | [frontend/chat](../../frontend/chat/README.md) |
| `/studio/` | 统计、完整历史、原文、候选审核、Claim与实验 | [frontend/studio](../../frontend/studio/README.md) |
| `/`，普通API请求 | 原JSON导航，包含chat/studio/docs/health | [网关](../../memoria/api/gateway.py) |
| `/app/` | 已移除，返回404及入口说明 | 不再挂载第二份Studio |
| `/assets/` | 一次多页面构建的带指纹静态资产 | frontend/dist/assets，Git忽略 |

统一使用frontend下package/lock/node_modules，Vite两个HTML输入输出dist/chat和dist/studio。聊天专用CSS与后台Element Plus分别打包，公共Vue/账号/API为共享块。以后新增前端功能独立子目录和入口，按需复用shared，不再新增一套散落根目录的构建配置。原根studio迁移完毕；此前迁入data/backups的旧React缓存、空目录/编译残留和旧Studio构建已于2026-10-14核验后由用户在本机清理，当前frontend源码和依赖保留。

## 对话与资料边界

Chat只展示本账号原生聊天；公开导入资料在Studio完整浏览。新对话按钮不写库，首次发送调用现有sessions创建和chat/stream接口，流式状态、回复和取消复用现有Agent。生成结束/出错/取消后重读实际保存内容；断流不自动重发。没有增加自由文本记忆自动批准或绕过Reader预算/引用校验。

Chat支持会话搜索和20条分页、每批50条历史/加载更早消息、移动端抽屉、输入法组合期间不触发Enter、纯文本安全显示；暂不包含Markdown富文本、附件、语音或外部SSO。Studio移除聊天发送/新建逻辑，保留后台阅读和治理。

## 登录与隐私

shared/auth.ts和AuthPanel共用登录、注册、正式HttpOnly Cookie、换号与退出广播。广播只有变化信号；API分通道取消及epoch拒绝迟到响应，X-Memoria-User来自服务端实际鉴权身份，读取正文/流前核对。退出清空双方业务状态和在途流，SSE每次读块/派发事件重验epoch，不能把旧账号流接入新账号。服务器UUID仍是权限边界，超管不自动读取他人原话。

## 启动与验证

[build_frontend.cmd](../../build_frontend.cmd)完成两端类型检查与构建；依赖缺失时仅安装frontend/node_modules；[启动](../../start_backend.cmd)/[停止](../../stop_backend.cmd)保持同一个2237后端进程。启动脚本显示Chat与Studio地址，并分别检查两个HTML构建入口；缺失时提示构建命令。详见[前端README](../../frontend/README.md)和[后台脚本说明](本机后台启动与停止.md)。

受影响后端92 passed / 41.90s（账号/隐私/投影/Studio/Reader/启动器），新增SSE保存与取消后投影文件7 passed / 11.13s；按不重复计数为94项工程通过。前端10项覆盖账号迟到响应、原文码点/投影、UTF-8/CRLF流边界、流中换号与异账号响应拒绝；类型检查和双入口生产构建通过。替身只在临时数据库验证流式协议及持久化/取消，不代表真实模型回复质量。真实浏览器验证根地址200显示独立Chat，Studio显示原50段/206消息；Chat原生聊天数为0，不填充公开导入资料。跨两端退出清空/重新登录恢复同账号通过；本机build_frontend.cmd实际构建通过。

静态检查72份Markdown/707个本地链接锚点、首页56个冻结数值与既有评测产物均通过；无凭据命中。构建脚本真实运行通过，生产根Chat/Studio/资产与匿名接口权限已核验。临时Vite开发服务器联调命令被本机自动执行策略拦截，尚未现场验收开发模式，不冒充已通过。

旧缓存此前因自动审批拦截而暂存；用户已执行本机清理，全部目标消失、保留文件指纹复核通过，见[清理完成记录](本机数据目录与残留清理.md)。本次统一根入口为cmd，当前源码与Git只保留新结构。

本次没有重跑LoCoMo或发起真实模型质量评测，生产账号对话/记忆不为测试写入，冻结结果不变。通用账号角色/后台资料实现、停止脚本与此次拆分按用户最新授权一同审查后普通推送main；本机账号密码/角色赋予状态和数据不发布。商业发布、原生连续资料长期效果及完整账号生命周期仍见[计划](../plans/长期陪伴记忆研究与实验计划书.md)。
