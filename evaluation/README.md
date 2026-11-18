# 线簿离线评测（重建）

依据：[dev-notes/evaluation/design.md](../dev-notes/evaluation/design.md)（含 **B.0 评测资产模型**）。原理故事：[how-it-works.md](../dev-notes/evaluation/how-it-works.md) · [故事图解](../dev-notes/evaluation/how-it-works.html)。结构讲解：[structure-walkthrough.md](../dev-notes/evaluation/structure-walkthrough.md) · [模块图解](../dev-notes/evaluation/structure-map.html)。工作台：[workbench-design.md](../dev-notes/evaluation/workbench-design.md)。旧 `eval/` 已拆除。启动工作台见 [SETUP](SETUP.md)。

## 能做什么

| 命令 | 作用 |
| --- | --- |
| `uv run python -m evaluation list` | 列出案例 |
| `uv run python -m evaluation run --base-url … --out …` | 对已启动的本地模式服务跑案例（可 `--suite`） |
| `uv run python -m evaluation score <运行目录>` | 用证据包重跑程序判分；若有 `reviews.json` 再合并人工复核 |
| `uv run python -m evaluation workbench` | 打开本机评测工作台（资产 / 运行 / 报告与质检） |

冒烟：`--mode smoke --repeat 1`（或 `--suite smoke_revise`）。完整门禁：`--mode gate --repeat 3` 或 `--suite gate_revise`，可用 `--baseline <旧 report.json>` 做质量退化对比。CLI 与工作台共用 `harness/runner_api.run_batch`。

## 目录（评测资产）

| 路径 | 内容 |
| --- | --- |
| `cases/` | Task：案例 YAML |
| `suites/` | Task 集：冒烟 / 门禁选题清单 |
| `envs/` | 评测环境档案 |
| `evaluators/` | Evaluator 包（检查项注册表） |
| `works/` | 审定后的基础作品 |
| `calibration/` | 评测器校准集占位（阶段后置） |
| `harness/` | schema / 资产加载 / 判分 / HTTP 运行时 / 证据包 / 报告 / `runner_api` |
| `workbench/` | 本机独立服务（不属于产品）；前端入口在 `frontend/eval-workbench.html` |

运行产物写到尚不存在的目录（如 `data/evaluation/runs/...`）。Job 元数据：`data/evaluation/jobs/<id>/job.json`。每次试验目录为 `attempts/<案例>--<次>/`，内含 `work/` 与 `evidence/`。`report.json` 含 `suite_id` / `env_id` / `evaluator_id` / `evaluator_version`，以及 `stages` / 检查项证据引用。`reviews.json` 含人工复核与归因。

工作台共享产品侧 Trace 展示组件。打开前先构建前端：`cd frontend && npm run build`；服务会托管 `frontend/dist/eval-workbench.html`。未构建时回退旧版静态页（仅报告表）。

工作台三区（hash）：

| 区 | 路由 | 作用 |
| --- | --- | --- |
| 资产 · Task 集 | `#/assets/suites` | Suite + 案例摘要（只读）；旧链 `#/cases` |
| 资产 · 评测环境 | `#/assets/envs` | Env 档案 |
| 资产 · Evaluator | `#/assets/evaluators` | 检查包 |
| 运行 · Pipeline | `#/pipeline` | 选 Suite/Env 触发跑测；旧链 `#/launch` |
| 报告与质检 | `#/runs` | 运行列表、单次 Trace/复核；资产基线展示 |

## 当前案例与 Suite

- 案例 `revise_ch1_ending_farm_tonight`：改第一章结尾为当晚去农场；Evaluator `revise_v1`；作品 `works/base/被解救的罪人`。
- Suite `smoke_revise` / `gate_revise`：上述首案的冒烟与门禁清单。

## 在线冒烟（作者）

1. 启动本地模式服务与前端（或仅服务）。
2. `cd frontend && npm run build`，再 `uv run python -m evaluation workbench`：在 Pipeline 选题并开始；或 CLI：  
   `uv run python -m evaluation run --base-url http://127.0.0.1:<端口> --suite smoke_revise --out data/evaluation/runs/smoke-1`
3. 在「报告与质检」打开本次运行，完成人工复核与归因后合并。可选 `--run smoke-1` 直接进入某次运行。

失败先修产品或案例，再单例复跑，不短时间大量重复。
