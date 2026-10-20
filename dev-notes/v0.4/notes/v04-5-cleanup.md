# V04-5：默认切换收尾与旧代码清理

日期：2026-10-20。范围：计划 M07、M08——默认切换的执行、旧代码与旧测试的清理、运行与验收说明（`release.md`）。默认切换已在「默认启动切换与旧数据库删除」中提前执行，本文记录清理部分。

## 目标

0.4 链路已经能跑，就不再让 0.3 的死代码留在仓库里：它们既无法启动，又会让「改哪里」的导航指向不存在的路径。清理后只剩一条链：入口 → 文件工作区 → Git + Agent + 界面。

## 实际改动

删除（后端 196 个模块、前端、测试与工具）：

- `domain/`、`application/`、`interfaces/`、`infrastructure/`、`agents/`、`bible/`、`tools/`、`prompts.py`、`session_store.py`、`session_persist.py`、`rag/`、`manuscript/`、`settings.py`、`events_store.py`、`context_manager.py`、`provider_catalog.py`；
- `server/app.py`、`rest.py`、`v2.py`、`ws.py`、`v2_ws.py`、`registry.py`、`session.py`、`hastings_session.py`、`v2_agents.py`、`model_access.py`、`ollama.py`，以及 `hastings.__main__`（CLI 入口）；
- 对应测试：`tests/v2/`（176 个文件）、`tests/fixtures/`、`tests/e2e_server.py`、`tests/fake_scripts.py` 与 33 个顶层测试模块；
- 前端：`api/rest.ts`、`v2.ts`、`types.ts` 等 11 个客户端、`state/v2*.ts`、`chat.ts`、`desk*.ts` 等 10 个状态、`components/desk/`、`components/chat/`、`components/ui/`（ErrorBoundary 改用普通按钮）、`ModelButton`、`ModelSettings`、`SettingsDrawer`、`Workbench`、`AgentOutputView`、`LocalModelCard`，以及它们的测试；
- `eval/driver.py`、`eval/v2/`、`eval/specs/`、`eval/intake/`（依赖已删的 0.3 模块）；`eval/runs`、`eval/assets` 作为历史证据保留，README 改写为归档说明；`scripts/` 三个 0.3 基准脚本删除；
- `e2e/` 的 0.3 旅程与走查脚本；`playwright.config.ts` 改为启动新服务并加 `e2e/workspace.spec.ts`。

保留且说明原因：

| 保留 | 原因 |
|---|---|
| `server/provider_config.py`、`infrastructure/credentials.py`、`application/ports/credentials.py`、`application/errors.py` | 模型档案与凭据，0.4 仍在用 |
| `domain/manuscript/diff.py`、`domain/repository/serialization.py`、`interfaces/importers/manuscript_text.py` | 导入功能仍在用；错误码 `IMPORT_ENCODING_UNSUPPORTED` 改为字面量 |
| `frontend/src/theme.ts` | Tailwind 取色的单一事实来源 |
| `frontend/src/components/ErrorBoundary.tsx`、`packaging/`、`desktop/main.ts`（已改为新启动参数） | 新壳层在用；打包入口仍有效 |

文档：

- 新增 [运行与验收说明](../modules/../release.md)；重写 `src/hastings/README.md`、`frontend/README.md`、`eval/README.md`；同步 `dev-notes` 各层导航与产品状态。
- `desktop/main.ts` 去掉 `--stories-root`/`--corpus-root`/`--config-dir`，改用 `--config`/`--model`/`--workspace`。

## 实现取舍

- **清理前先用依赖反查**，从入口模块做传递闭包，再删闭包之外的文件，而不是按目录砍。第一次执行时路径分隔符不匹配（脚本用 `Path` 字符串与清单里的正斜杠比较），误删了整棵树，用 `git checkout -- src/hastings` 完整恢复后改为规范化路径重跑；这次经验记录在备忘里。
- **没有内联遗留小模块**：`domain/manuscript/diff.py`、`repository/serialization.py`、`importers/manuscript_text.py` 三处仍在用，内联到 `workspace/` 里要动导入与行为，收益低风险高，保留并在导航里注明。
- **0.3 文档保留**：删除代码不等于删除决策记录；文档顶部的状态已标明「描述的实现已删除」。
- **`data/v2.db` 此前已删除，M07 的旧作品导出因此没有对象**，按作者当时的选择处理。

## 验证

| 范围 | 命令 | 结果 |
|---|---|---|
| 后端 | `CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q` | 130 项通过（0.4 用例，约 4 分钟） |
| 类型与静态检查 | `uv run mypy src/hastings`、`uv run ruff check src/hastings tests/workspace` | 28 个源文件，全部通过 |
| 前端单元 | `cd frontend && npm test` | 3 个文件 17 项通过 |
| 前端构建 | `npm run build` | 通过（CSS 36.9 kB → 19.9 kB，JS 353 kB → 328 kB） |
| 浏览器 E2E | `npx playwright test` | 5 项通过（目录、只读稿纸、历史与撤销、搜索与外部刷新、新建作品） |

清理后没有残留引用：`grep` 全仓库对已删模块名的引用已清零（`eval/README.md` 与本文档中的说明除外）。

## 偏差与未完成

- **真实模型未取证**，同 V04-4。
- **`data/test-tmp/` 下的历史走查脚本与截图**部分随临时目录清理丢失；归档在 `dev-notes/v0.4/artifacts/walkthrough-v04-4/` 的部分保留。
- **`desktop/dist/` 未重新构建**：`main.ts` 已改，但打包产物仍是旧的（`desktop/dist` 不入库）；下次打包时生效。
- **前端 `package.json` 仍有 0.3 依赖**（`@radix-ui/*`、`@xyflow/react`、`d3-force`、`lucide-react`、`class-variance-authority`、`clsx`、`tailwind-merge` 等）：卸载依赖要动锁文件，未在本次执行。
