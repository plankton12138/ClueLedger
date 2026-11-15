# 线簿离线评测（重建）

依据：[dev-notes/evaluation/design.md](../dev-notes/evaluation/design.md)。旧 `eval/` 已拆除，本目录为新实现。

## 能做什么

| 命令 | 作用 |
| --- | --- |
| `uv run python -m evaluation list` | 列出案例 |
| `uv run python -m evaluation run --base-url … --out …` | 对已启动的本地模式服务跑案例，写出报告与证据包 |
| `uv run python -m evaluation score <运行目录>` | 用证据包重跑程序判分；若有 `reviews.json` 再合并人工复核 |
| `uv run python -m evaluation workbench` | 打开本机评测工作台；页面里选择已落盘运行（默认扫 `data/evaluation/runs`） |

冒烟：`--mode smoke --repeat 1`。完整门禁：`--mode gate --repeat 3`，可用 `--baseline <旧 report.json>` 做质量退化对比。

## 目录

| 路径 | 内容 |
| --- | --- |
| `cases/` | 案例 YAML |
| `works/` | 审定后的基础作品 |
| `harness/` | schema / 判分 / HTTP 运行时 / 证据包 / 报告 |
| `workbench/` | 本机独立服务（不属于产品）；前端入口在 `frontend/eval-workbench.html` |

运行产物请写到尚不存在的目录（如 `data/evaluation/runs/...`，已由根目录 `data/` 忽略）。每次试验目录为 `attempts/<案例>--<次>/`，内含 `work/`（运行后作品）与 `evidence/`（`before.json`、`diff.json`、`events.jsonl`、`traces/`、`inputs/`）。`report.json` 中每次试验带 `stages` / `first_failed_stage`，检查项 `evidence` 为引用列表（file / span / input / missing）。`reviews.json` 含人工复核与归因。

工作台共享产品侧 Trace 展示组件。打开前先构建前端：`cd frontend && npm run build`；服务会托管 `frontend/dist/eval-workbench.html`。未构建时回退旧版静态页（仅报告表）。

## 当前案例

- `revise_ch3_ending_snow`：改第三章结尾为雪夜，保留对话；作品 `works/base/inn`（手写 v1）。

## 在线冒烟（作者）

1. 启动本地模式服务与前端（或仅服务）。
2. `uv run python -m evaluation run --base-url http://127.0.0.1:<端口> --case revise_ch3_ending_snow --out data/evaluation/runs/smoke-1 --mode smoke`
3. `cd frontend && npm run build`，再 `uv run python -m evaluation workbench`：在列表里点开本次运行，从失败检查项点到 Trace，完成人工复核与归因后合并。可选 `--run smoke-1` 直接进入某次运行。

失败先修产品或案例，再单例复跑，不短时间大量重复。
