# 默认启动切换与旧数据库删除

日期：2026-10-20。范围：按作者要求删除旧工作台数据库，并把 `python -m hastings.server` 的默认启动改为 0.4 文件工作区。这属于 V04-5「默认切换」的一部分，提前执行；旧代码清理与旧作品导出未做。

## 决定

作者明确要求「删掉旧版本数据库，并保持默认启动为新版本」，并在两项确认中选定：

- 旧数据库**直接删除**，不做导出；
- **彻底切换**：不保留 `--legacy` 之类的回退参数，旧工作台不能再从命令行启动。

这两项都偏离了修改计划里「旧数据库只读保留、V04-5 再切换」的安排，记录在此以便日后核对。

## 实际改动

- 删除 `data/v2.db`、`data/v2.db-shm`、`data/v2.db-wal`。库内有 11 个项目，名称均为测试与走查产物（`test1`、`test2`、`测试项目`、`走查预检`、`走查想法一…`、`诊断想法…`、`hi` 等），删除前未导出，0.3 作品不可恢复。
- 删除前发现有一个旧版服务进程（`python -m hastings.server`）正在运行并占用数据库文件，先停止该进程再删除。
- 重写 `src/hastings/server/__main__.py`：只装配文件工作区应用；`--workspace` 改为可选的「启动时预先打开」，不再是启用开关；移除 `--v2-db`、`--stories-root`、`--corpus-root`、`--config`、`--config-dir`、`--model`。
- 删除 `tests/test_server_main.py` 与 `tests/test_server_production_loop.py`：它们验证的是已退出的旧入口与旧生产装配。
- `tests/workspace/test_entry.py` 扩为入口测试：参数、静态目录、无目录启动、绑定目录、旧路由 404、不创建数据库、退出码。

## 契约与数据影响

- 默认启动不再读 `config.yaml`、不再创建或打开 `data/v2.db`、不再挂 `/api/v2` 与旧模型路由。
- 旧参数传入即按参数错误退出（退出码 2），不静默忽略；目录或前端目录不存在退出码 1。
- 旧接口（`/api/v2`、`/api/workspaces`、`/api/model-selection`）在新应用上返回 404。
- 应用状态改为 `~/.hastings`（可用 `HASTINGS_STATE_DIR` 覆盖），只记最近目录。
- 桌面壳 `desktop/` 仍按旧参数启动服务，切换后无法启动；本次未改，留待 V04-4/V04-5。

## 验证

| 范围 | 命令 | 结果 |
|---|---|---|
| 文件工作区与入口 | `uv run pytest tests/workspace -q` | 74 项通过 |
| 完整后端回归 | `CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q --junitxml=data/test-tmp/v04-switch.xml` | 2049 项通过，0 失败 0 错误 0 跳过 |
| 静态检查 | `uv run ruff check src/hastings tests/workspace` | 通过 |
| 类型检查 | `uv run mypy src/hastings` | 268 个文件无问题 |

测试总数比 V04-1 少 12 项，差额来自删除的旧入口测试；被删测试对应的能力已退出，不表示质量提升。未做真实浏览器走查：前端仍是 0.3 构建，接新接口前页面不可用。

## 未完成

- V04-2 Git 历史、V04-3 Agent 与对话、V04-4 前端接线均未实现；当前默认应用只有文件读取、搜索与导入。
- 0.3 的源码（`domain/`、`application/`、`interfaces/`、`server/app.py`、`server/rest.py`、`server/v2.py` 等）与其测试仍在仓库中，已无启动入口，待 V04-5 清理。
- 旧作品导出工具（M07）未做，且已无旧库可导。
- `desktop/` 启动参数与版本号未同步。
