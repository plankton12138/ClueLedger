# 评测工作台六入口壳 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把本机评测工作台从「资产 / 运行 / 报告与质检」三区壳改成六个一级入口，并让报告先进总览、质检独立成台、Pipeline 露出可比基线与归因分流。

**Architecture:** 后端只新增跨 run 的 `GET /api/qa/queue`（聚合 `report.json` + `reviews.json`），写回仍走现有 reviews API。前端把路由与顶栏改为六入口，复用 `AssetsPage` / `LaunchPage` / Trace 下钻，按页改布局与默认视图；不改 harness 证据包主结构。

**Tech Stack:** Python 3（`evaluation/workbench`）、pytest + httpx；React + Vite（`frontend/src/eval-workbench`）、现有 vitest（`marks.test.ts` 同模式）。

**Spec:** [workbench-design.md §4.0](../workbench-design.md)；[design.md B.0 / B.8](../design.md)

## Global Constraints

- 阶段 4 前：Task / Env / Evaluator **只读** YAML，不提供创建/编辑器。
- 不做 LLM Judge 编辑器、完整校准流水线、跨 run 趋势图、多 Job 并行。
- 工作台独立入口，不进产品路由；评测后端不 `import` 产品业务模块。
- 产品文案与报错格式：`[错误码] 位置：现象。建议：…`；对外名称用「线簿」，不用 Hastings。
- 本机 Windows / CI Linux：测试勿无条件 mock 仅 Windows API。
- 提交前按 ci-preflight：相关 pytest → ruff check → ruff format --check → mypy → `frontend` `npm run build`（仅文档改动可跳过后端全量）。

## Review Focus

- 旧 hash（`#/assets/*`、`#/runs`、`#/launch`、`#/cases`）打开后应落到新页，不能空白。
- `GET /api/qa/queue` 在某 run 缺 `reviews.json` 或 report 损坏时仍返回其他 run 的队列，不 500。
- 已人工 `pass`/`fail` 的 human 项不再出现在「待复核」摘要里。
- Pipeline「确认可比」在未选 Suite 或 Evaluator 时禁止开跑，并给出可读错误。
- 报告列表进 `#/reports/<id>` 时默认 `overview`，不要一进就进 attempt Trace。

## File map

| 文件 | 职责 |
| --- | --- |
| `evaluation/workbench/data.py` | 新增 `build_qa_queue(runs_root) -> dict` |
| `evaluation/workbench/server.py` | 注册 `GET /api/qa/queue` |
| `tests/evaluation/test_workbench.py` | 队列 API 测试 |
| `frontend/src/eval-workbench/routing.ts` | `parseHash` / `writeHash` / `Route`（从 App 抽出） |
| `frontend/src/eval-workbench/routing.test.ts` | 路由与重定向单测 |
| `frontend/src/eval-workbench/App.tsx` | 六入口顶栏；挂载各页；报告默认 overview |
| `frontend/src/eval-workbench/AssetsPage.tsx` | 按入口改成表格/分栏布局（可拆文件，见 Task 3） |
| `frontend/src/eval-workbench/LaunchPage.tsx` | Pipeline 四步 UI + 可比确认 |
| `frontend/src/eval-workbench/QaPage.tsx` | 新建：质检队列页 |
| `frontend/src/eval-workbench/api.ts` | `fetchQaQueue` 等 |
| `frontend/src/eval-workbench/workbench.css` | 分步条、指标墙、队列表样式 |
| `dev-notes/evaluation/workbench-design.md` | 实施记录勾「已落地」 |

---

### Task 1: 质检队列 API

**Files:**
- Modify: `evaluation/workbench/data.py`
- Modify: `evaluation/workbench/server.py`
- Test: `tests/evaluation/test_workbench.py`

**Interfaces:**
- Consumes: `list_runs`, `load_reviews_file`, `read_json`, report `attempts[].checks`
- Produces: `build_qa_queue(runs_root: Path) -> dict[str, Any]`；`GET /api/qa/queue` JSON：

```python
{
  "summary": {"pending": int, "reviewed": int, "eval_bug": int},
  "items": [
    {
      "run_id": str,
      "case_id": str,
      "attempt": int,
      "check_id": str,
      "level": str,  # human | must | quality
      "machine_verdict": str,
      "human_verdict": str | None,  # pass|fail|uncertain|None
      "attribution_category": str | None,
      "reason": str,
    },
    ...
  ],
}
```

入队规则：检查项 `level == "human"` 且（无人工 verdict，或人工为 `uncertain`）；可选同时列入 `machine_verdict == "fail"` 且尚无归因的 must 失败（便于归因台，标 `level` 原值）。`eval_bug` 计数：`attributions` 里 `category == "eval_bug"`（若现有类别名不同，与 `attribution-options` 对齐，通常为评测判错对应值——实现前读 `/api/attribution-options` 与 App 归因选项，用真实 category id）。

- [ ] **Step 1: Write the failing test**

在 `tests/evaluation/test_workbench.py` 追加：

```python
def test_workbench_qa_queue(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    _seed_report(runs_root / "demo")
    # 第二 run：已复核 human，不应计入 pending
    run_b = runs_root / "done"
    _seed_report(run_b)
    (run_b / "reviews.json").write_text(
        json.dumps(
            {
                "reviews": [
                    {
                        "case_id": "revise_ch1_ending_farm_tonight",
                        "attempt": 1,
                        "check_id": "farm_tonight",
                        "verdict": "pass",
                        "evidence_ref": "after:x",
                    }
                ],
                "attributions": [
                    {
                        "case_id": "revise_ch1_ending_farm_tonight",
                        "attempt": 1,
                        "category": "eval_bug",
                        "note": "判错",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    server, base = _serve(runs_root)
    try:
        with httpx.Client(base_url=base, timeout=5.0, trust_env=False) as client:
            payload = client.get("/api/qa/queue").json()
            assert payload["summary"]["pending"] >= 1
            assert payload["summary"]["eval_bug"] >= 1
            pending_ids = {
                (i["run_id"], i["check_id"])
                for i in payload["items"]
                if i.get("human_verdict") in (None, "uncertain")
                and i.get("level") == "human"
            }
            assert ("demo", "farm_tonight") in pending_ids
            assert ("done", "farm_tonight") not in pending_ids
    finally:
        server.shutdown()
        server.server_close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evaluation/test_workbench.py::test_workbench_qa_queue -v`  
Expected: FAIL（404 或无 `build_qa_queue`）

- [ ] **Step 3: Implement `build_qa_queue` + route**

在 `data.py` 实现聚合；在 `server.py` `do_GET` 中：

```python
if path == "/api/qa/queue":
    self._send_json(200, wb.build_qa_queue(runs_root))
    return
```

损坏的单个 run：跳过并继续（可用 `try/except` 包 `read_json`）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evaluation/test_workbench.py::test_workbench_qa_queue -v`  
Expected: PASS

- [ ] **Step 5: Commit**（仅在用户要求提交时执行）

```bash
git add evaluation/workbench/data.py evaluation/workbench/server.py tests/evaluation/test_workbench.py
git commit -m "feat(eval-workbench): add cross-run QA queue API"
```

---

### Task 2: 路由抽取与六入口重定向

**Files:**
- Create: `frontend/src/eval-workbench/routing.ts`
- Create: `frontend/src/eval-workbench/routing.test.ts`
- Modify: `frontend/src/eval-workbench/App.tsx`（改用导出的 `parseHash` / `writeHash`）

**Interfaces:**
- Produces:

```ts
export type Route =
  | { page: "tasks" }
  | { page: "envs" }
  | { page: "evaluators" }
  | { page: "pipeline"; presetCaseIds: string[]; suiteId: string | null }
  | { page: "reports"; runId: string | null; preferCaseId?: string | null }
  | { page: "qa" };

export function parseHash(hash?: string): Route;
export function writeHash(route: Route): void;
```

`parseHash` 映射：`assets/suites|cases`→`tasks`；`assets/envs`→`envs`；`assets/evaluators`→`evaluators`；`launch|pipeline`→`pipeline`；`runs`→`reports`；`qa`→`qa`；默认 `#/reports`。

- [ ] **Step 1: Write failing vitest**

```ts
import { describe, expect, it } from "vitest";
import { parseHash } from "./routing";

describe("parseHash", () => {
  it("maps legacy assets and runs", () => {
    expect(parseHash("#/assets/suites")).toEqual({ page: "tasks" });
    expect(parseHash("#/assets/envs")).toEqual({ page: "envs" });
    expect(parseHash("#/assets/evaluators")).toEqual({ page: "evaluators" });
    expect(parseHash("#/cases")).toEqual({ page: "tasks" });
    expect(parseHash("#/runs/smoke-3")).toEqual({
      page: "reports",
      runId: "smoke-3",
      preferCaseId: null,
    });
    expect(parseHash("#/launch")).toEqual({
      page: "pipeline",
      presetCaseIds: [],
      suiteId: null,
    });
    expect(parseHash("#/qa")).toEqual({ page: "qa" });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/eval-workbench/routing.test.ts`  
Expected: FAIL（模块不存在）

- [ ] **Step 3: Implement `routing.ts` and wire App**

从 `App.tsx` 挪出 `parseHash`/`writeHash`/`Route`；`writeHash` 写出 `#/tasks`、`#/envs`、`#/evaluators`、`#/pipeline…`、`#/reports…`、`#/qa`。App 内导航与 `navigate` 改用新 `Route`（删除 `assets` / `cases` / `runs` 页名，逻辑迁到新名）。

- [ ] **Step 4: Run tests**

Run: `cd frontend && npx vitest run src/eval-workbench/routing.test.ts`  
Expected: PASS；并 `npx tsc --noEmit`（或项目既有 typecheck）确认 App 编译。

- [ ] **Step 5: Commit**（仅在用户要求提交时执行）

```bash
git add frontend/src/eval-workbench/routing.ts frontend/src/eval-workbench/routing.test.ts frontend/src/eval-workbench/App.tsx
git commit -m "feat(eval-workbench): six-entry hash routes with legacy redirects"
```

---

### Task 3: 顶栏六入口 + 三资产页布局

**Files:**
- Modify: `frontend/src/eval-workbench/App.tsx`（`wb-nav` 六个 chip）
- Modify: `frontend/src/eval-workbench/AssetsPage.tsx`（或拆 `TasksPage.tsx` / `EnvsPage.tsx` / `EvaluatorsPage.tsx`）
- Modify: `frontend/src/eval-workbench/workbench.css`

**Interfaces:**
- Consumes: Task 2 `Route`；现有 `fetchSuites` / `fetchEnvs` / `fetchEvaluators` / `fetchCases`
- Produces: 三页只读浏览；`onLaunchSuite` / `onLaunchCase` → `pipeline`

布局（对照 spec §4.0.1）：
- Task：表格列「名称 / 模式 / 案例数 / 更新 / 跑」；行可展开案例摘要（可复用现有 detail）。
- Env：左列表 + 右档案。
- Evaluator：必需（`role !== "diagnostic"` 或 `level === "must"`）与诊断分栏。

- [ ] **Step 1: 改顶栏为六入口**

```tsx
<nav className="wb-nav">
  <button className={...} onClick={() => navigate({ page: "tasks" })}>Task集</button>
  <button className={...} onClick={() => navigate({ page: "envs" })}>评测环境</button>
  <button className={...} onClick={() => navigate({ page: "evaluators" })}>Evaluator</button>
  <button className={...} onClick={() => navigate({ page: "pipeline", presetCaseIds: [], suiteId: null })}>Pipeline</button>
  <button className={...} onClick={() => navigate({ page: "reports", runId: null })}>评测报告</button>
  <button className={...} onClick={() => navigate({ page: "qa" })}>人工质检</button>
</nav>
```

先可用占位：`tasks|envs|evaluators` 仍渲染现有 `AssetsPage` 并传入对应 tab。

- [ ] **Step 2: 调整 AssetsPage 布局**

去掉「资产」二级 Tab 条（入口已在顶栏）；Suite 列表改为 table；Evaluator 按 must/quality（或 role）两组渲染。

- [ ] **Step 3: 手动核对**

启动工作台，点六个入口均有内容；从 Task「跑」进入 Pipeline 且带 `suite` query。

- [ ] **Step 4: Commit**（仅在用户要求提交时执行）

```bash
git add frontend/src/eval-workbench/App.tsx frontend/src/eval-workbench/AssetsPage.tsx frontend/src/eval-workbench/workbench.css
git commit -m "feat(eval-workbench): six-entry nav and asset page layouts"
```

---

### Task 4: Pipeline 分步 + 可比确认

**Files:**
- Modify: `frontend/src/eval-workbench/LaunchPage.tsx`
- Modify: `frontend/src/eval-workbench/workbench.css`
- Modify: `frontend/src/eval-workbench/api.ts`（若 Job 创建需显式 `evaluator_id`——已有则只展示）

**Interfaces:**
- Consumes: 现有 `createJob`；Suite 的 `default_env` / `default_evaluator`
- Produces: 四步 UI 状态机 `step: 1|2|3|4`

步骤：
1. **选资产**：Suite 或案例、Env、模式、repeat（与现表单字段相同）。
2. **确认可比**：只读条显示 `suite_id` / `env_id` / `evaluator_id` / mode / baseline；缺 Suite（且无案例）或缺 Env 时「开始」禁用，文案说明缺什么；若选了 baseline，提示「请确认与基线 Task+Evaluator 一致」。
3. **执行**：调用现有 `createJob`。
4. **进度**：现有 job 进度 / 取消 / 打开 run（打开走 `#/reports/<id>`）。

- [ ] **Step 1: 在 LaunchPage 增加 `step` state 与步进条**

```tsx
const steps = ["选资产", "确认可比", "执行", "进度"] as const;
// 顶部渲染 steps；step>=2 显示身份条
```

- [ ] **Step 2: 把现有表单拆进 step 1；step 2 只读确认；提交成功后 `setStep(4)`**

- [ ] **Step 3: 手动跑一遍冒烟选 Suite → 确认 → 开跑（或 dry：故意缺 Env 看禁用）**

- [ ] **Step 4: Commit**（仅在用户要求提交时执行）

```bash
git add frontend/src/eval-workbench/LaunchPage.tsx frontend/src/eval-workbench/workbench.css
git commit -m "feat(eval-workbench): pipeline stepper with comparability confirm"
```

---

### Task 5: 评测报告默认总览 + 指标墙

**Files:**
- Modify: `frontend/src/eval-workbench/App.tsx`（原 runs 页改为 `reports`；进 run 默认 `view === "overview"`）
- Modify: `frontend/src/eval-workbench/workbench.css`

**Interfaces:**
- Consumes: 现有 `report.gate` / `summary` / `baseline_compare` / 矩阵
- Produces: 列表页 `#/reports`；详情先进指标墙（门禁、通过率、阻断 must 数、基线 Δ、Suite/Env/Evaluator），其下为已有矩阵；点格进 attempt。

- [ ] **Step 1: 把 runs 列表/详情渲染分支改名为 reports，保证 `setView("overview")` 在加载 report 时触发（已有则核对 preferCase 才进 attempt）**

规则：仅当 URL 带 `?case=` 时进入 attempt 并选中该案；否则 overview。

- [ ] **Step 2: 在 overview 顶部加指标墙四格**

用现有 `gate.passed`、`summary`、`baseline_compare`、blockers 长度；身份条显示 `suite_id` / `env_id` / `evaluator_id`。

- [ ] **Step 3: `npm run build`（frontend）确认通过**

- [ ] **Step 4: Commit**（仅在用户要求提交时执行）

```bash
git add frontend/src/eval-workbench/App.tsx frontend/src/eval-workbench/workbench.css
git commit -m "feat(eval-workbench): report KPI overview before trace drill-down"
```

---

### Task 6: 人工质检页 + 归因分流

**Files:**
- Create: `frontend/src/eval-workbench/QaPage.tsx`
- Modify: `frontend/src/eval-workbench/api.ts`（`fetchQaQueue`）
- Modify: `frontend/src/eval-workbench/App.tsx`（挂载 `#/qa`；归因保存后跳转）
- Modify: 单次运行归因区（App 内）：保存后按 category 调用 `navigate`

**Interfaces:**
- Consumes: Task 1 `GET /api/qa/queue`；现有 `PUT .../reviews`
- Produces: 质检台摘要 + 表格；行点击 → `#/reports/<run>?case=<case_id>`

归因分流（与 spec 一致；category 以 `/api/attribution-options` 为准）：
- 产品类（如 `tool` / `model` / …）→ `navigate({ page: "pipeline", suiteId, presetCaseIds: [] })` 并尽量带上 report 的 suite/env（可用 query 扩展；最小：pipeline + suiteId）。
- `eval_bug`（或选项中「评测判错」）→ `navigate({ page: "evaluators" })`。
- 「不是问题」→ 仅写回。

- [ ] **Step 1: api.ts 增加**

```ts
export type QaQueue = {
  summary: { pending: number; reviewed: number; eval_bug: number };
  items: Array<{
    run_id: string;
    case_id: string;
    attempt: number;
    check_id: string;
    level: string;
    machine_verdict: string;
    human_verdict: string | null;
    attribution_category: string | null;
    reason: string;
  }>;
};
export function fetchQaQueue() {
  return api<QaQueue>("/api/qa/queue");
}
```

- [ ] **Step 2: 实现 QaPage 并挂到 App**

- [ ] **Step 3: 归因写回成功后按 category 跳转**

- [ ] **Step 4: 手动：打开 `#/qa` 能看到 Task 1 测试同构数据形态；从队列点进报告**

- [ ] **Step 5: Commit**（仅在用户要求提交时执行）

```bash
git add frontend/src/eval-workbench/QaPage.tsx frontend/src/eval-workbench/api.ts frontend/src/eval-workbench/App.tsx
git commit -m "feat(eval-workbench): QA queue page and attribution handoff"
```

---

### Task 7: 收尾核验与文档状态

**Files:**
- Modify: `dev-notes/evaluation/workbench-design.md` §9 实施记录（六入口改为已落地）
- Modify: `dev-notes/evaluation/design.md` B.8.3（水位改为已落地）

- [ ] **Step 1: 跑相关测试**

```bash
uv run pytest tests/evaluation/test_workbench.py -v
cd frontend && npx vitest run src/eval-workbench && npm run build
```

- [ ] **Step 2: 按 ci-preflight 补跑**（若改了 Python）

```bash
uv run ruff check evaluation/workbench tests/evaluation/test_workbench.py
uv run ruff format --check evaluation/workbench tests/evaluation/test_workbench.py
uv run mypy
```

- [ ] **Step 3: 更新设计文档状态行**

- [ ] **Step 4: Commit**（仅在用户要求提交时执行）

```bash
git add dev-notes/evaluation/workbench-design.md dev-notes/evaluation/design.md
git commit -m "docs(evaluation): mark six-entry workbench shell as landed"
```

---

## Spec coverage (self-check)

| Spec 项 | Task |
| --- | --- |
| 六入口导航与路由/旧链重定向 | 2, 3 |
| Task/Env/Evaluator 页布局 | 3 |
| Pipeline 分步 + 可比 | 4 |
| 报告先总览再下钻 | 5 |
| 质检队列 API + 页 | 1, 6 |
| 归因分流 | 6 |
| 不做 YAML/LLM Judge/趋势 | 全局约束；未排任务 |
| 复用 Trace/Job | 4–6 不重写 harness |

## Placeholder scan

无 TBD；归因 category 字符串以实现时读取的 `attribution-options` 为准（Task 1/6 已写明）。
