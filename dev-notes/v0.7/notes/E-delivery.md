# E：迁移、回归与交付

目标：撤掉旧入口与无调用组件，跑回归，写模块与运行说明，升级版本。

## 实际改动

- 删除 `ChapterIndexPanel.tsx`、`StatePanel.tsx`；前端不再调用 `/chapter-index` 与
  `/state` 的六个取数函数与对应类型（后端端点保留以兼容既有调用）。
- 版本升级：`pyproject.toml`、`frontend/package.json`、`frontend/package-lock.json`、
  `desktop/package.json`、`src/hastings/__init__.py` 统一为 `0.7.0`；`uv lock` 已刷新。
- 文档：新增 `modules/story-materials.md`、`modules/README.md`、`release.md` 与 A–E 阶段
  记录；更新 `dev-notes/README.md`、`src/hastings/README.md`、`dev-notes/v0.7/README.md`
  的现状指向。
- 新增走查脚本 `frontend/e2e/walkthrough-v07-materials.mjs`，截图归档在
  `artifacts/walkthrough-v07/`；在线样例归档在 `artifacts/online-*.json`。

## 接口与数据影响

- 旧 `/state`、`/chapter-index`、已保存会话事件继续可读；旧事件缺 `origin` 时按写作
  会话处理。
- 迁移不自动补跑：没有覆盖信息的旧记录显示「未整理」，升级时不调用模型。

## 取舍

- 不重写活动记录、版本历史与模型设置：本版只调整主页面与资料页，其他入口保持原契约。
- 旧检查回看继续可用，但不开放独立「检查报告」页面。

## 验证

| 项目 | 结果 |
|---|---|
| 后端全量 `uv run pytest -q` | 578 passed |
| `ruff check src/hastings tests/workspace` | 通过 |
| `mypy src/hastings` | 通过 |
| 前端 `npx vitest run` | 126 passed |
| 前端 `npm run build` | 通过 |
| 浏览器走查（不调用模型） | 通过，截图 9 张 |
| 在线模型 `deepseek-flash` | 3 个样例通过（时间线有发现/无发现、章节概览） |

## 未完成

- 人物、线索、名词的在线语义验收。
- 任务运行中停止、断线重连、服务重启、预算耗尽的浏览器取证。
- 双窗口并发与 150% 字号走查。
