# GitHub 提交规范

本文件由根目录 [AGENTS.md](../../AGENTS.md) 引用。支持 AGENTS.md 的 AI 工具会自动获得根指令，再按任务读取本规范；其他工具应显式读取 AGENTS.md。它是项目工作说明，不是后台 Git 钩子，也不保证所有 AI 客户端都自动识别。

## 远程、身份和分支

- 远程：`https://github.com/ZJ331456/Memoria-Agent.git`。
- 本项目当前使用 `main`，遵循用户“直接推送 main、不要新建分支”的约定；后续用户明确变更时以最新要求为准。
- author / committer：`ZJ331456 <ZJ331456@users.noreply.github.com>`。Git 作者和远程认证账号是两个概念，分别检查。
- 仅在用户授权提交或更新远程的工作中执行推送；不要把“自动读取”理解为每次访问都自动提交。

## 提交消息

格式：`<type>: <中文说明，可含英文技术名词>`。使用 feat / fix / docs / refactor / perf / test / chore / build / ci / style / revert；描述具体问题或结果，避免 update、fix bug 等空泛内容。

例如：`feat: 接入本地网页搜索与 GitHub MCP`；`refactor: 移除聊天前端与合成实验数据`。一次提交围绕一个可解释的变化，代码、文档与对应验证一起提交。

## 执行顺序

1. `git status --short`、`git branch --show-current`、`git remote -v`，检查工作区和 main。
2. 使用仓库局部配置确认身份；读取已有未提交修改，不能覆盖无关工作。
3. 实现功能，更新 implemented 文档；未完成项写到 plans，不宣称完成。
4. 项目 `.venv` 内运行受影响测试，检查 `git diff --check` 和实际 diff。
5. 精确暂存本次文件；检查暂存内容不含凭据、数据库、运行日志、原始个人数据或模型权重。MCP 依赖锁文件可以提交，安装目录不能提交。
6. 按上述格式 commit；`git fetch origin main` 后确认可以普通 fast-forward 推送。发生分歧先审查并整合，禁止 force push。
7. `git push origin main`；核对本地 HEAD 与 `git ls-remote origin refs/heads/main` 的 SHA 相同，检查最终 status。

## 环境及大文件

仅安装到 `.venv` / `.mcp`，不污染 base。模型、密钥、token、数据库及原始对话均保存在被忽略的本地目录；公开语料提交来源、版本、SHA 和选样清单，由下载脚本复现。

运行报告写明真实样本数、失败分母、来源与实际模型，不提交模拟轨迹成绩。不能因真实 MCP 调用需要授权，就填入虚假 App ID/token 或将演示返回值记为成功。

官方读取行为参考：[Codex AGENTS.md 指南](https://developers.openai.com/codex/guides/agents-md)。
