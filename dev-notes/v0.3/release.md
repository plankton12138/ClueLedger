# Hastings 0.3 发布记录与运行说明

> 版本：**0.3.0**（pyproject 已统一为 0.3.0）
> 状态：**功能面交付完成（V03-0–V03-8.7）；完整验收未过——0.3 不标「完整交付」**。
> 依据：[33 项验收追踪](artifacts/v03-8.8-acceptance-tracking.md)。未验项见 §5，未列入的项不视为通过。
> 记录日期：2026-10-19，基线 commit `2aa7138`（V03-8.7）。

## 1. 交付范围

- **三批 0.3 能力**：写作台三部分工作区、统一发布与操作记录、导入预览、手改副本与三方合并、撤销、账本候选、背景装配与章摘要、披露边界、意图三分与覆盖台账、预算与修订任务、四类图谱与时间线、执行日志、备份恢复、生产装配、视觉与可访问性、旧入口退役。
- **文档**：52 篇构建笔记（`dev-notes/v0.3/notes/`）、API 矩阵、模块 as-built（`modules/`）、本发布记录。
- **质量门（2026-10-19）**：`uv run pytest -q` **1975 passed**；`ruff check src/hastings tests` 全过；`mypy src/hastings` 261 files 无问题；前端 `npm run build` 通过、`vitest` **433 passed**；真模型走查 **6 通过 + 1 失败证据**；视觉走查 **37/37**。

## 2. 运行说明（当前实现）

### 2.1 环境

- Python 3.12（`uv run` 统一入口）；Node 22（前端）。
- 模型：`config.yaml` 的 provider 档案（本机为 `ollama` → `http://localhost:11434/v1`）；或 `.env` 的 `OPENAI_BASE_URL/OPENAI_API_KEY/HASTINGS_MODEL` 回落。
- 凭据只进服务端凭据存储；评测报告不回显 Key。

### 2.2 后端

```bash
uv run python -m hastings.server            # 默认 127.0.0.1:8000，装配 V2 REST/WS/SSE
uv run python -m hastings.server --v2-db data/v2.db   # 指定数据库（默认 data/v2.db）
```

- 启动即执行 `recover_on_startup()`：执行中的轮次/任务标中断，旧自动队列隔离为 `historical_paused`（**不自动重发模型调用**）；取证 `GET /api/v2/recovery`。
- 未配置模型时服务照起、可写（不阻塞本地文字工作）。

### 2.3 前端

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173，/api 与 /ws 反代到 :8000
npm run build      # 生产产物 frontend/dist，由后端 / 静态托管
```

### 2.4 测试与评测

```bash
uv run pytest                    # 全量后端
uv run pytest tests/v2           # V2 领域/接口
uv run ruff check src/hastings tests/v2
uv run mypy
cd frontend && npm test          # vitest 433
cd frontend && npx playwright test   # v2-*.spec.ts（真实服务旅程，端口 8931）

# 真模型走查（本地 Ollama，无收费 API）
uv run python eval/v2/desk_driver.py --out eval/runs/<日期>-v03-8.7 --model qwen3:8b
# 含真模型修订全循环（读章→改稿→覆盖→暂存→发布）
uv run python eval/v2/desk_driver.py --out eval/runs/<日期>-v03-8.7-deep --model qwen3:8b --deep
```

### 2.5 数据与恢复

- 数据库：`data/v2.db`（SQLite）；服务级旧工作区在 `stories/`；语料在 `corpus/`（0.3 不消费）。
- 备份：`GET /api/v2/projects/{id}/backup`（预览 + 导出包）；恢复：`POST /api/v2/projects/{id}/restore`（未确认差异 409）。

## 3. 发布记录

| 日期 | 版本 | 内容 |
|---|---|---|
| 2026-10-19 | 0.3.0 | V03-8.1–8.7 收口：启动恢复/备份恢复、生产装配、日志压力、视觉走查、旧入口退役、真模型实测；文档 as-built 与 33 项追踪 |
| 2026-10-19 | 0.3.0 补丁 | 试写改写现有章自动装入该章正文（`drafting.create` → `previous_text`）；`--deep` 复跑 10 通过 + 2 证据；全量 1987 passed |

## 4. 兼容与迁移

- `/api/v2` 与存储版本独立于产品版本号；0.3 默认工作区为写作台。
- 旧工作区数据保留只读；旧自动队列任务不复活，需作者显式继续。
- 旧写入（导入/保存/恢复）全部经统一发布，返回 `operation_id`。

## 5. 未验项（显式）

> 2026-10-19 补验：§9.4 十场景与雪山旅馆夹具已建并跑通（10/10，确定性模型替身，全量 **1985 passed**），第三批阶段退出已有端到端证据；`--deep` 真模型修订全循环 10 通过 + 2 失败证据。下列为**剩余**未验项。

1. **真 uvicorn 进程启动无运维日志证据**（8.3 冒烟与验收套件都是同进程 TestClient）。
2. **前端执行日志面板消费端未接**（接口齐备，无前端读取方）。
3. **V1 语料写接口仍可手工调用**（0.3 无入口、无自动消费）。
4. **节拍类型退出未做**（计划 §9.1 留白；起草入口已不要求 Beat）。
5. 远程 Provider（OpenAI）无真实 Key，未调用；真 Key 链路证据缺失。
6. ~~试写候选对「现有章」不带原文~~ **已修复（2026-10-19）**：`drafting.create` 现在把该章当前正文作为 `previous_text` 交给提示词（段落 `现有正文`），不截断；`--deep` 复跑时 instruction 不含原文仍能保留原文细节。剩余口径：覆盖读取登记仍由发起方做（试写不绑定 revision-work）。
7. **整体任务预算无消费方**：`RevisionTaskService.consume_calls` 无生产调用方，真实模型调用未被计入 `calls_used`（`--deep` 中恒为 0）；预算账本目前是记账面，不是执行门禁。
8. 夹具层两处**专项边界**：证据锚点失效的 `needs_recheck` 需正文引文锚（V03-2.5 服务层覆盖）；披露定位型 409 需叙事结构（V03-4.7 服务层覆盖）。

## 6. 结论

0.3 的功能与工程面已交付；**十场景与三批退出已有可复跑证据**（夹具 + 套件 10/10、全量 1985 passed、视觉走查 37/37）。
真模型侧：基础走查 6 通过 + 1 失败证据，`--deep` 修订全循环 10 通过 + 2 失败证据（读章→改稿→覆盖→暂存→一次发布）。
按计划 §9.7 的完成定义，仍有未验项（§5）：真 uvicorn 日志、前端日志面板、V1 语料写接口、节拍类型、远程 Provider，以及 `--deep` 暴露且尚未接线的任务预算消费方（试写读原文已修）。
对外表述为「**0.3 功能交付，验收补验中（运维取证 + 预算接线）**」，不写「完整交付」。
