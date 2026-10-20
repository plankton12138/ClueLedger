# V04-0：旧版本合并与 0.4 计划开立

日期：2026-10-20。范围：收拢 0.3 工作区改动、合并旧版本分支、开立 0.4 方案与开发计划；不实现 0.4 运行功能。

## 分支与文件结果

- 原分支 `v03-0-baseline` 比原 `main` 领先 95 个提交，原 `main` 无独立提交。
- 5 个已有 0.3 修改经核对后提交为 `3c47a95`：对话失败原因与重试入口、对应测试、远程模型首段等待预算及写作台说明。
- 切换旧 `main` 时发现其曾跟踪运行中的数据库。先用 `git rm --cached` 解除三个数据库文件的跟踪，提交为 `2334c81`；磁盘上的 `data/v2.db`、`-shm`、`-wal` 保留，没有提交当前数据库内容。
- 通过普通合并提交 `93c196f` 将 `v03-0-baseline` 合入 `main`。合并后 `git diff v03-0-baseline HEAD` 为空，文件树与已收拢的 0.3 分支一致。
- 从合并后的 `main` 创建 `codex/v0.4`；保留旧分支，不删除历史。未配置 Git remote，本次仅在本地合并，没有推送。
- 未跟踪的 `mynotes/` 保持原样，未读取内容、未加入提交。

## 本次验证

| 范围 | 命令 | 结果 |
|---|---|---|
| 写作台失败展示与对话重试 | `npm test -- src/components/desk/DeskShell.test.tsx src/state/chat.test.ts`，在 `frontend/` 下 | 2 个文件、27 项通过 |
| 模型绑定与对话运行器 | `uv run --no-sync --cache-dir data/.uv-cache pytest tests/v2/agents/test_model_binding.py tests/v2/conversation/test_runner.py -q --basetemp data/test-tmp/v04-baseline-20261020` | 17 项通过 |
| 前端类型检查与构建 | `npm run build`，在 `frontend/` 下 | 通过；存在既有模块同时静态与动态导入的构建提示 |
| 旧版改动空白检查 | `git diff --check` | 通过 |
| 新方案与相关导航链接 | 检查本次涉及的 9 份 Markdown 中本地链接目标是否存在 | 84 个链接全部存在；不等于运行功能验证 |

默认 uv 缓存和默认 pytest 临时目录最初因访问权限失败；最终使用仓库内被忽略的缓存、临时目录完成上述后端验证。未修改依赖或应用配置。

以上只证明本次相关 0.3 回归范围，不等于旧版本完整验收，也不包含真实模型、独立服务或浏览器完整走查。0.4 尚未实现，不能引用这些结果作为 0.4 验收。

## 文档与未完成项

建立 [0.4 产品方案](../../../product-design/v0.4/README.md)、[开发入口](../README.md)和[修改计划](../development-plan.md)，更新相关导航，明确目标与当前实现的区别。

包版本仍为 `0.3.0`；首个运行实现阶段再同步调整为 `0.4.0`。文件工作区、Git 历史、直接写文件 Agent、新接口和前端接线均待实施。旧数据库和旧作品没有迁移。
