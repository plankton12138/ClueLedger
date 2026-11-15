# 阶段 1：拆除旧评测 · 实施计划

日期：2026-11-14。状态：**已完成**。依据：[评测体系重建设计](design.md) 第 1、11 节。

**Goal:** 删除旧 `eval/` 与 `tests/evaluation/`，切断产品/脚本对它们的依赖，并修正当前文档入口；不迁移旧数据与旧案例。

**Architecture:** 旧 harness 整体丢弃。仅把仍被 e2e / 脚本使用的「读 `.env` + 判断在线端点」两段纯函数挪到 `scripts/online_env.py`，避免删除后在线验收夹具断裂。

## 1. 范围

### 做

| 项 | 说明 |
| --- | --- |
| 抽出 `read_env` / `online_endpoint` | 新文件 `scripts/online_env.py`；更新 `frontend/e2e/fixtures/story_creation_server.py`、`scripts/online_acceptance_config.py` |
| 删除目录 | 整个 `eval/`、`tests/evaluation/` |
| 当前文档 | `REVIEW.md`、`dev-notes/agent-interview.md`、`dev-notes/README.md` |
| 导航说明 | 在 `dev-notes/README.md` 注明历史版本文档中的 `eval/` 链接已随拆除失效 |

### 不做

- 不新建新评测 harness（阶段 2）
- 不逐份改写 v0.1—v1.1 历史验收文中的 `eval/` 路径
- 不迁移 `eval/runs`、案例 YAML、旧报告

## 2. 验收

- `uv run pytest tests/workspace/test_activity.py -q` 仍通过（抽查与评测无关的回归入口）
- 对 `from eval` / `import eval` 的全仓检索仅余历史文档（`dev-notes/v*` 等），无 Python 源引用
- `REVIEW.md` 不再把 `eval/` 当作现行评测代码路径

## 3. 实施记录（2026-11-14）

| 项 | 结果 |
| --- | --- |
| `scripts/online_env.py` | 承接 `read_env` / `online_endpoint`；`online_acceptance_config` 与构思 e2e 夹具已改引用 |
| 删除 | `eval/`、`tests/evaluation/` 整目录移除（不迁移） |
| 文档 | `REVIEW.md`、`agent-interview.md`、`dev-notes/README.md`、`.gitignore` |
| 抽查 | 活动套件 30 passed；`.env` 三字段可读 |
