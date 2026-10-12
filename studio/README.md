# Memory Studio：专用记忆工作室

[项目首页](../README.md) · [行为与验证](../docs/implemented/MemoryStudio与统一记忆访问.md) · [API](../memoria/api/README.md)

Vue 3 + TypeScript + Vite + Element Plus，连接正式登录网关和真实账号数据；没有聊天窗、预置画像或演示历史。

## 四个页面

| 页面 | 当前能力 |
| --- | --- |
| Memory Explorer | 原话/span 高亮、槽位/主体/条件/极性、明确审核与修订 |
| Temporal Explorer | 事实/获知独立时间、中文日历区间、时间线/未知解释 |
| Retrieval Lab | unified/Claim/keyword/BM25/Dense/RRF、真实候选/过滤、同池 top-k/coverage、整组字符预算 |
| Evaluation Lab | 当前账号规范 manifest 和已核验执行 metrics；只读、不启动脚本 |

公开语料/非本人用户原话/撤销来源不可创建个人 Claim。明确开启的模型支持度是建议，不自动批准。真实空账号保持空状态，不填模拟数据。

## 安装、构建、启动

在本目录，依赖只装 studio/node_modules：

```powershell
npm ci
npm run build
npm test
```

本机 2026-10-12 核对 Node 22.16.0/npm 10.9.2；test 使用 --experimental-strip-types，旧 Node 可能不支持。build 同时 vue-tsc/Vite，dist 不提交，部署重新构建。

构建后在仓库根启动/重启：

```powershell
.\.venv\Scripts\python.exe main.py --host 127.0.0.1 --port 2237
```

打开 `http://127.0.0.1:2237/studio/`，用自己的已注册账号；未构建返回 503，根仍 JSON，/docs 为 Swagger。开发时后端保持 2237，在 studio 执行 npm run dev，访问 `http://127.0.0.1:5173/studio/`；[vite.config.ts](vite.config.ts) 将 /api 代理至 2237，Vite 不是生产入口。

## 文件与隐私

[App.vue](src/App.vue) 负责页面/明确审核，[api.ts](src/api.ts) 同源 Cookie/401 清理/跨账号迟到响应拒绝，[style.css](src/style.css) 样式。原话按文本渲染，span 按 Unicode 码点；不持久保存密码、Bearer 或模型密钥，登出/失效清空状态。

后端 UUID 是权限边界，客户端检查不替代鉴权。账本不返原始 gold/prompt/密钥，独立 score 修订/逐题钻取/全研究管理待实现；字符非 reader token，时间线非长期效果证明。

## 验证记录

[tests/api.test.ts](tests/api.test.ts) 检查码点、跨账号延迟和401；替身不进产品/研究。2026-10-12 既有验收为 3 项前端测试、vue-tsc/生产构建通过，并通过隔离浏览器真实 HTTP 链路及账号只读核验。本次 README 整理不将历史验收冒充新运行，后端见 [tests/](../tests/README.md)。
