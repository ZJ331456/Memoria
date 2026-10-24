# 前端：Chat 与 Memory Studio

[项目首页](../README.md) · [功能说明](../docs/implemented/Chat与Studio前端架构.md)

两套独立页面、一套依赖与构建。根地址 `http://127.0.0.1:2237/` 是聊天界面；`/studio/` 是资料、记忆治理与实验后台。旧 `/app/` 已移除。

```text
frontend/
├─ chat/                 # 聊天页面与专用样式
├─ studio/               # 数据后台与管理组件
├─ shared/               # Cookie登录、账号生命周期、API、SSE与原文工具、设计token(tokens.css)
├─ ../.zcode/skills/     # 三个前端skill（taste/frontend-design/frontend-ui-engineering），指导前端实现
├─ tests/                # 跨账号竞态、文本与流式解析工程测试
├─ package.json          # 两端统一依赖
├─ package-lock.json
├─ vite.config.ts        # 两个HTML入口
└─ dist/                 # 构建产物，Git忽略
```

## 构建与启动

Windows入口统一在scripts/cmd：双击[build_frontend.cmd](../scripts/cmd/build_frontend.cmd)，或从仓库根执行`.\scripts\cmd\build_frontend.cmd`。缺少node_modules时按锁文件安装到本目录，再类型检查和构建；`-Install`强制重新按锁安装。之后用[start_backend.cmd](../scripts/cmd/start_backend.cmd)启动；后端正在运行且代码有修改时先[停止](../scripts/cmd/stop_backend.cmd)。依赖不装到base或全局npm，Linux入口仍预留在[sh](../scripts/sh/README.md)。

手动方式在本目录运行：

```powershell
npm ci --cache .npm-cache --no-audit --no-fund
npm run build
npm test
```

开发时后端保持2237，在本目录运行 `npm run dev`：Chat 为 `http://127.0.0.1:5173/`，Studio 为 `http://127.0.0.1:5173/studio/`。`/api`代理到2237，部署只需2237与dist，无需Vite进程。Vite根中间件仅把开发根入口映射到chat/index.html。

## 扩展约定

新页面放新的子目录，以独立HTML/main/style入口注册到vite.config.ts，并在网关明确托管；通用鉴权/API放shared。不要复制另一套登录状态、把用户ID交给客户端选择，或把某个页面的全局CSS加载到另一个页面。新增依赖统一更新根package/lock；不在子目录分别生成node_modules和锁文件。

Chat只列本账号原生聊天，导入公共语料在Studio查看。两端共享HttpOnly Cookie、退出广播、请求取消与响应归属检查；凭据与原话不写浏览器持久存储。旧React缓存及旧Studio构建已迁入忽略的本地备份，根studio目录已移除，Git不保留并行旧入口。

[Chat模块](chat/README.md) · [Studio模块](studio/README.md) · [共享层](shared/README.md)

Chat/Studio共用当前账号改密弹窗及安全Markdown组件；消息排版、外链/图片边界与临时浏览器验收见[前端说明](../docs/implemented/Chat与Studio前端架构.md#账号改密与聊天阅读2026-10-15)。依赖仅安装frontend目录，jsdom仅供工程DOM/XSS验证，不进入生产模型或实验语料。

账号操作统一收进用户名菜单（Chat左下、Studio右上）；登录/注册表单共用。Studio的日常管理与研究诊断分组、来源→候选→明确批准、记忆版本/导出及只读处理状态见[当前交付](../docs/implemented/Chat与Studio前端架构.md#账号菜单与记忆处理可见性2026-10-15)。
