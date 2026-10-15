# 前端共享层

[目录与构建](../README.md) · [账号隔离](../../docs/implemented/用户登录与数据隔离.md)

- `auth.ts`：Chat/Studio共用账号生命周期、登录/注册/退出、跨标签变化信号和重新激活校验。
- `AuthPanel.vue`：共享账号表单，不保存密码或令牌。
- `FeishuPanel.vue`：只读连接状态、本账号一次性绑定码和明确解绑；不公开应用凭据，不持久化绑定码。
- `PasswordPanel.vue`：验证旧密码的当前账号改密弹窗，关闭/成功/换号清空字段，成功撤销旧登录并通知两个界面重新认证。
- `MarkdownContent.vue` / `markdown.ts`：保存和流式消息共享的Markdown渲染与HTML白名单，图片不自动加载；来源原文不改写。
- `api.ts`：同源Cookie、401清理、请求分通道取消、旧响应丢弃、服务端归属核对及增量SSE解析。
- `console.ts`：日期、字面高亮、有限中文阅读投影；source指纹和Unicode范围始终使用原文。

各端只维护自己的业务状态，通过账号clear钩子全部释放。新增网络接口沿用API类，不能绕过服务端租户网关；SSE每次读取/派发事件都检查账号epoch，UTF-8与CRLF跨块可正确处理。共享层不引入Element Plus或页面全局样式，Chat不加载Studio的组件库包。

- `AccountMenu.vue`：把当前用户名、改密、切换账号、飞书与退出集中展示；超管元数据入口可由Studio启用。
- `MemoryCandidateDialog.vue`：从当前账号的有效本人原话提出待审候选；不调用模型，不绕过公开语料/角色/撤销检查。

AuthPanel提供登录/注册切换、确认密码、可见性与大写锁定提示。手动候选、Markdown导出与其他请求沿用相同的epoch及响应归属校验。

MemoryCandidateDialog提交只创建候选，后台按当前llm/manual策略处理；默认低风险可自动入库，文案不再承诺每条必经手工批准。
