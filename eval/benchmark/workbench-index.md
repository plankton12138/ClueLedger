# 工作台只读索引（P0）

从仓库根目录运行（需安装 dev 依赖）：

```sh
uv run python -m eval.benchmark.workbench_index
uv run python -m eval.benchmark.workbench_index --archive eval/data/runs/T01/2026-11-21/index.json
```

第一条列出仓库自带的 `writing` 库；第二条额外加载上述本地历史摘要（档案目录不入库，新检出没有该文件）。命令不运行 Agent、不启动服务、不写入档案。`--offset` / `--limit` 分别作用于任务列表和各档案分区。

## 任务目录

用重复的 `--library 库ID=目录` 指定多个库；一旦显式指定，替换默认配置。库按目录平铺读取 `*.json`，定义使用 [Task Schema v1](task-schema.md)，不向 Task JSON 增加库字段。目录以 `library_id / task_id / revision` 唯一定位；列表只列每库每题的最新修订，按库和题号稳定排序。`Catalog.list()` 支持库过滤、题号/标题搜索与分页；`revisions()` 与 `detail()` 可查历史修订。

加载校验 Schema、重复 JSON 键和身份、步骤与消息引用、检查点及每步必过项。有错即停止，不静默漏列；合法目录条目不等于可运行，外置素材、作品初态、模型与产品入口仍由未来 Runner 预检。

## 档案清单 `archive_version: 1`

清单是 `{ "archive_version": 1, "runs": [...] }`。每条记录要求 `run_id`（全清单唯一）、`kind`（`task` 或 `regression`）、`execution_status`（`not_started`、`running`、`completed`、`failed` 或 `unknown`）。任务运行须有 `task` 对象（`library_id`、`task_id`、`revision`）；独立回归不填，避免纳入该题统计。历史任务引用不要求当前目录仍保留该修订。

可选字段：`started_at`（有时区的 ISO 8601 执行开始时间）、`verdict`（`pass`、`fail`、`inconclusive`、`invalid`）、`title`、`turn_id`、`model`。未知字段或非法值会报错；旧档缺失字段留空。不要在此存储模型凭据、稿件或完整 Trace。

`Archive.list()` 先过滤任务修订、运行类别和时间分区，再排序、分页：

- `started`：按开始时间实际所指的时刻升序，同一时刻按 `run_id`；不看文件顺序、复核时间或判定。
- `not_started`：明确尚未启动；`unknown`：未记录实际开始时间。两区按 ID 排列，不纳入执行顺序。

`Archive.detail(run_id)` 读取该次运行摘要。后续重判保留原 `run_id` 和 `started_at`；新一次执行才分配新 ID。P0 不负责导入原始证据、写入报告或解析 Trace；这些继续由 [工作台计划](../../dev-notes/evaluation/development-plan.md) 后续阶段交付。
