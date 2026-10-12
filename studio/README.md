# Memoria Studio

Vue 3 + TypeScript + Vite + Element Plus 专用记忆界面。四页连接真实API：来源/候选审核、双时间修订、检索解释、只读执行账本；没有聊天窗、演示画像或预置用户数据。

```powershell
# 在此目录操作，只安装项目依赖
npm ci
npm run build
npm test
# 可选开发代理到2237
npm run dev
```

dist不入Git；项目根用.venv启动HTTP，访问`http://127.0.0.1:2237/studio/`。未构建时503提示构建，根路径仍是JSON导航。用已有登录账号，不在客户端保存密码、Bearer或模型密钥。

[行为、API、隐私与验证边界](../docs/implemented/MemoryStudio与统一记忆访问.md)。src/api.ts阻止跨账号延迟响应；src/App.vue只发送明确审批/修订，模型分析可选显式调用；Unicode span按码点高亮。Node测试替身只验证客户端边界，不生成生产/研究数据。
