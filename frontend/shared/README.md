# 前端共享层

[目录与构建](../README.md) · [账号隔离](../../docs/implemented/用户登录与数据隔离.md)

- `auth.ts`：Chat/Studio共用账号生命周期、登录/注册/退出、跨标签变化信号和重新激活校验。
- `AuthPanel.vue`：共享账号表单，不保存密码或令牌。
- `FeishuPanel.vue`：只读连接状态、本账号一次性绑定码和明确解绑；不公开应用凭据，不持久化绑定码。
- `api.ts`：同源Cookie、401清理、请求分通道取消、旧响应丢弃、服务端归属核对及增量SSE解析。
- `console.ts`：日期、字面高亮、有限中文阅读投影；source指纹和Unicode范围始终使用原文。

各端只维护自己的业务状态，通过账号clear钩子全部释放。新增网络接口沿用API类，不能绕过服务端租户网关；SSE每次读取/派发事件都检查账号epoch，UTF-8与CRLF跨块可正确处理。共享层不引入Element Plus或页面全局样式，Chat不加载Studio的组件库包。
