import { expect, test } from "@playwright/test";

const key = { library_id: "writing", task_id: "T01", revision: 2 };
const entry = { ...key, title: "隔离测试任务" };
const task = (revision: number) => ({
  scenario: {
    id: "T01", revision, title: entry.title, description: "验证新作品",
    environment: { model: "online" }, messages: { start: `作者输入 r${revision}` },
    cases: { main: { title: "副本一", fixture: { files: {}, focus: { kind: "work_home" } }, steps: [{ id: "first", message: "start" }] } },
  },
  code_graders: { saved: { checkpoint: { case: "main", step: "first" }, role: "required", evidence: ["workspace_after"], rule: { type: "workspace_diff" } } },
  llm_graders: {},
});

test("窄屏筛选任务、切换修订并返回原筛选", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 850 });
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/tasks") {
      expect(route.request().method()).toBe("GET");
      const filtered = url.searchParams.get("library_id") === "writing" && url.searchParams.get("query") === "T01";
      return route.fulfill({ json: { items: filtered ? [entry] : [entry, { ...entry, library_id: "other", task_id: "T02", title: "另一库任务" }], libraries: ["writing", "other"], has_more: false } });
    }
    if (url.pathname.startsWith("/api/tasks/writing/T01/")) {
      const revision = Number(url.pathname.split("/").pop());
      return route.fulfill({ json: { task: task(revision), revisions: [1, 2], runnable: false, reason: "任务未启用" } });
    }
    return route.fulfill({ json: { items: [], has_more: false } });
  });
  await page.goto("/workbench.html");
  await page.getByRole("link", { name: "任务库", exact: true }).click();
  await page.getByLabel("任务库筛选").selectOption("writing");
  await page.getByLabel("搜索任务").fill("T01");
  await expect(page.getByText("另一库任务")).toHaveCount(0);
  await page.getByRole("link", { name: /隔离测试任务/ }).click();
  await expect(page.getByText("作者输入 r2")).toBeVisible();
  await expect(page.getByLabel("搜索任务")).toBeHidden();
  await page.getByLabel("任务修订").selectOption("1");
  await expect(page.getByText("作者输入 r1")).toBeVisible();
  await expect(page).toHaveURL(/library_id=writing&task_id=T01&revision=1/);
  await page.reload();
  await expect(page.getByText("作者输入 r1")).toBeVisible();
  await page.getByRole("link", { name: "返回任务列表" }).click();
  await expect(page.getByLabel("搜索任务")).toHaveValue("T01");
  await expect(page.getByLabel("任务库筛选")).toHaveValue("writing");
  await page.getByRole("link", { name: /隔离测试任务/ }).click();
  await page.getByRole("link", { name: "核对运行配置" }).click();
  await expect(page.getByText("不可运行 · 任务未启用")).toBeVisible();
  await expect(page.getByRole("button", { name: "执行预检" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "开始运行" })).toBeDisabled();
});

const initial = { run_id: "first", kind: "task", task: key, title: "首次运行", started_at: "2026-11-21T00:12:00Z", execution_status: "failed", verdict: "invalid" };
const baseline = { ...initial, run_id: "baseline", title: "有效基线", started_at: "2026-11-21T00:15:00Z", execution_status: "completed", verdict: "inconclusive" };
const noTime = { run_id: "old", kind: "regression", title: "无开始时间的旧档", execution_status: "unknown" };
const runDetail = {
  run: baseline, review_status: "pending", evidence_sha256: "bound-digest", review_inputs: {},
  report: { verdict: "inconclusive", checks: [{ check_id: "saved", case_id: "main", step_id: "first", verdict: "unknown", reason: "首次快照缺失", evidence_refs: ["workspace_after/STRUCTURE.md", "trace/retry"] }] },
  milestones: [{ type: "capture", summary: "补录取证" }], evidence_versions: ["original", "corrected"], report_versions: ["r1", "r2"], available_evidence: ["workspace_before", "workspace_after", "assistant_reply"],
};
const traceEvent = { span_id: "write", parent_span_id: null, kind: "tool_call", name: "首次写入", start_offset_ms: 0, duration_ms: 10, point: false, status: "failed", summary: "格式门槛拦截", start_event_id: "event-first", finish_event_id: null, started_at: null, finished_at: null, error_code: "FORMAT", usage: null, retry_of_span_id: null };

test("保留执行顺序，按判定引用定位证据和同轮重试", async ({ page }) => {
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    expect(route.request().method()).toBe("GET");
    switch (url.pathname) {
      case "/api/runs": {
        const timing = url.searchParams.get("timing");
        return route.fulfill({ json: { items: timing === "started" ? [initial, baseline] : timing === "unknown" ? [noTime] : [], default_run_id: "baseline", has_more: false } });
      }
      case "/api/tasks/writing/T01/2": return route.fulfill({ json: { task: task(2), revisions: [2], runnable: false } });
      case "/api/runs/baseline": return route.fulfill({ json: url.searchParams.get("report_version") === "r1" ? { ...runDetail, report: null } : runDetail });
      case "/api/runs/baseline/evidence": return route.fulfill({ json: url.searchParams.get("version") === "original" ? { workspace_before: {} } : { workspace_before: {}, workspace_after: { "STRUCTURE.md": "archived-hash" } } });
      case "/api/runs/baseline/trace": return route.fulfill({ json: { events: [traceEvent, { ...traceEvent, span_id: "retry", status: "completed", name: "重试写入", summary: "已保存", error_code: null, retry_of_span_id: "write", start_event_id: "event-retry" }], details: { "event-retry": { args: { result: "archived trace detail" } } } } });
      default: return route.fulfill({ status: 404, json: { detail: "未记录" } });
    }
  });
  await page.goto("/workbench.html");
  const archive = page.getByRole("region", { name: "执行时间顺序" });
  await expect(archive.getByRole("link")).toHaveText([/首次运行/, /有效基线/]);
  await expect(archive.getByRole("link", { name: /有效基线/ })).toHaveAttribute("aria-current", "true");
  await expect(page.getByRole("region", { name: "执行时间未记录" })).toContainText("无开始时间的旧档");
  await expect(page.getByText("补录取证")).toBeVisible();
  await page.getByRole("button", { name: "workspace_after/STRUCTURE.md", exact: true }).click();
  await expect(page.getByText("archived-hash", { exact: true })).toBeVisible();
  await page.getByLabel("证据版本").selectOption("original");
  await page.getByText("运行前文件快照", { exact: true }).click();
  await expect(page.getByText("已记录为空", { exact: true })).toBeVisible();
  await expect(page.getByText("archived-hash")).toHaveCount(0);
  await page.getByRole("button", { name: "workspace_after/STRUCTURE.md", exact: true }).click();
  await expect(page.getByText("archived-hash", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "trace/retry", exact: true }).click();
  await expect(page.getByRole("button", { name: /重试写入/ })).toHaveAttribute("aria-current", "true");
  await expect(page.getByRole("button", { name: /首次写入/ })).toBeVisible();
  await expect(page.getByText(/archived trace detail/)).toBeVisible();
  await page.getByRole("checkbox", { name: "仅看问题" }).check();
  await expect(page.getByRole("button", { name: /重试写入/ })).toHaveCount(0);
  await page.getByLabel("报告版本").selectOption("r1");
  await expect(page.getByText("此版本没有逐项报告。", { exact: true })).toBeVisible();
});

test("预检拒绝时不启动；明确启动后直接轮询新运行", async ({ page }) => {
  const approvalToken = "opaque-approval/from-preflight+=测试";
  let preflights = 0;
  let starts = 0;
  let reads = 0;
  await page.route("**/api/**", async route => {
    const { pathname } = new URL(route.request().url());
    const req = route.request();
    if (req.method() === "POST") {
      expect(req.headers()["x-workbench-intent"]).toBe("local");
      if (pathname === "/api/preflight") {
        expect(req.postDataJSON()).toEqual(key);
        preflights++;
        return route.fulfill({ json: preflights === 1 ? { valid: false, reason: "没有可用模型" } : { valid: true, approval_token: approvalToken, model: "configured-model", connection_id: "workbench-test", evaluator_version: "test-v1" } });
      }
      expect(pathname).toBe("/api/runs");
      expect(req.postDataJSON()).toEqual({ ...key, approval_token: approvalToken });
      starts++;
      return route.fulfill({ status: 202, json: { run_id: "fresh" } });
    }
    if (pathname === "/api/tasks/writing/T01/2") return route.fulfill({ json: { task: task(2), revisions: [2], runnable: true } });
    if (pathname === "/api/runs/fresh") {
      reads++;
      return route.fulfill({ json: { ...runDetail, run: { ...baseline, run_id: "fresh", title: "新运行", started_at: null, execution_status: reads === 1 ? "running" : "completed" } } });
    }
    return route.fulfill({ json: { items: [], has_more: false } });
  });
  await page.goto("/workbench.html?page=launch&library_id=writing&task_id=T01&revision=2");
  await expect(page.getByText("隔离测试任务", { exact: false })).toBeVisible();
  const start = page.getByRole("button", { name: "开始运行", exact: true });
  await expect(start).toBeDisabled();
  expect(preflights).toBe(0);
  await page.getByRole("button", { name: "执行预检" }).click();
  await expect(page.getByText("没有可用模型")).toBeVisible();
  await expect(start).toBeDisabled();
  expect(starts).toBe(0);
  await page.getByRole("button", { name: "执行预检" }).click();
  await expect(page.getByText("configured-model", { exact: true })).toBeVisible();
  expect(starts).toBe(0);
  await start.click();
  await expect(page).toHaveURL(/run_id=fresh/);
  await expect(page.getByText("已完成", { exact: true })).toBeVisible();
  expect(starts).toBe(1);
  expect(reads).toBeGreaterThanOrEqual(2);
});

test("运行详情遇到 503 时保留运行状态，退避后继续轮询", async ({ page }) => {
  let reads = 0;
  await page.clock.install({ time: new Date("2026-11-21T00:20:00Z") });
  await page.clock.pauseAt(new Date("2026-11-21T00:21:00Z"));
  await page.route("**/api/**", async route => {
    const { pathname } = new URL(route.request().url());
    if (pathname === "/api/runs/baseline") {
      reads++;
      if (reads === 2 || reads === 3) return route.fulfill({ status: 503, json: { detail: `服务暂不可用 ${reads}` } });
      return route.fulfill({ json: { ...runDetail, run: { ...baseline, execution_status: reads === 1 ? "running" : "completed" } } });
    }
    return route.fulfill({ json: { items: [], has_more: false } });
  });
  await page.goto("/workbench.html?run_id=baseline");
  const inspector = page.getByRole("region", { name: baseline.title });
  const running = inspector.getByText("执行中", { exact: true });
  await expect(running).toBeVisible();
  await page.clock.runFor(900);
  await expect(page.getByRole("alert")).toContainText("服务暂不可用 2");
  await expect(running).toBeVisible();
  await page.clock.runFor(900);
  expect(reads).toBe(2);
  await page.clock.runFor(900);
  await expect(page.getByRole("alert")).toContainText("服务暂不可用 3");
  await expect(running).toBeVisible();
  await page.clock.runFor(1800);
  expect(reads).toBe(3);
  await page.clock.runFor(1800);
  await expect(inspector.getByText("已完成", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.clock.runFor(5000);
  expect(reads).toBe(4);
});

test("复核绑定证据，拒绝过期标注后读回新报告", async ({ page }) => {
  let attempts = 0;
  let readsAfterWrite = 0;
  const reviewedCheck = { check_id: "usable", verdict: "pass", reason: "稿件符合要求", evidence_refs: ["structure_text"] };
  const before = {
    ...runDetail, evidence_sha256: "old-digest",
    review_inputs: { usable: { rule: { criterion: "检查已保存稿件" }, evidence_refs: ["structure_text"] } },
  };
  await page.route("**/api/**", async route => {
    const { pathname } = new URL(route.request().url());
    const req = route.request();
    if (pathname === "/api/runs/baseline/review") {
      expect(req.method()).toBe("POST");
      expect(req.headers()["x-workbench-intent"]).toBe("local");
      attempts++;
      const { check_id: _id, ...input } = reviewedCheck;
      expect(req.postDataJSON()).toEqual({ evidence_sha256: attempts === 1 ? "old-digest" : "changed-digest", checks: { usable: input } });
      if (attempts === 1) return route.fulfill({ status: 409, json: { detail: "证据已更新，请重新复核" } });
      return route.fulfill({ json: { ...before, review_status: "pending" } });
    }
    if (pathname === "/api/runs/baseline/evidence") return route.fulfill({ json: { structure_text: "现场保存的稿件" } });
    if (pathname === "/api/runs/baseline") {
      if (attempts === 2) {
        readsAfterWrite++;
        return route.fulfill({ json: { ...before, evidence_sha256: "changed-digest", review_status: "reviewed", report: { verdict: "inconclusive", checks: [reviewedCheck] } } });
      }
      return route.fulfill({ json: { ...before, evidence_sha256: attempts ? "changed-digest" : "old-digest" } });
    }
    return route.fulfill({ json: { items: [], has_more: false } });
  });
  await page.goto("/workbench.html?run_id=baseline");
  await page.getByRole("tab", { name: "人工复核" }).click();
  await expect(page.getByText("检查已保存稿件")).toBeVisible();
  const save = page.getByRole("button", { name: "提交人工复核" });
  await expect(save).toBeDisabled();
  const fill = async () => {
    await page.getByLabel("结论 · usable").selectOption("pass");
    await page.getByLabel("理由 · usable").fill(reviewedCheck.reason);
  };
  await fill();
  await save.click();
  await expect(page.getByRole("alert")).toContainText("证据已更新，请重新复核");
  await expect(page.getByText("changed-digest", { exact: true })).toBeVisible();
  await expect(save).toBeDisabled();
  await fill();
  await save.click();
  await expect(page.getByText("已复核", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "逐项判定" })).toContainText(reviewedCheck.reason);
  await expect(page.getByText("待定", { exact: true }).first()).toBeVisible();
  expect(readsAfterWrite).toBeGreaterThanOrEqual(1);
});

test("历史报告不显示当前报告的人工复核内容", async ({ page }) => {
  const current = {
    ...runDetail,
    evidence_sha256: "current-review-digest",
    review_inputs: { usable: { rule: { criterion: "当前复核规则" }, evidence_refs: ["structure_text"] } },
    report: { verdict: "pass", checks: [{ check_id: "usable", verdict: "pass", reason: "当前复核理由", evidence_refs: ["structure_text"] }] },
  };
  await page.route("**/api/**", async route => {
    const { pathname, searchParams } = new URL(route.request().url());
    if (pathname === "/api/runs/baseline") {
      return route.fulfill({ json: searchParams.get("report_version") === "r1"
        ? { ...runDetail, report_version: "r1" } : current });
    }
    if (pathname === "/api/runs/baseline/evidence") return route.fulfill({ json: { structure_text: "当前证据" } });
    if (pathname === "/api/tasks/writing/T01/2") return route.fulfill({ json: { task: task(2), revisions: [2], runnable: true } });
    return route.fulfill({ json: { items: [], has_more: false } });
  });
  await page.goto("/workbench.html?run_id=baseline");
  await page.getByRole("tab", { name: "人工复核" }).click();
  const review = page.getByRole("tabpanel", { name: "人工复核" });
  await expect(review).toContainText("当前复核理由");
  const draft = review.getByLabel("理由 · usable");
  await draft.fill("还未提交的理由");
  await page.getByLabel("报告版本").selectOption("r1");
  await expect(page.getByRole("region", { name: "逐项判定" })).toContainText("首次快照缺失");
  await expect(review).not.toContainText(/current-review-digest|当前复核规则|当前复核理由/);
  await expect(page.getByRole("button", { name: "提交人工复核" })).toHaveCount(0);
  await page.getByLabel("报告版本").selectOption("");
  await expect(review).toContainText("当前复核理由");
  await expect(draft).toHaveValue("还未提交的理由");
});

test("比较显示差异；选择证据重判读回同一运行", async ({ page }) => {
  let regrades = 0;
  let readsAfterWrite = 0;
  await page.route("**/api/**", async route => {
    const { pathname, searchParams } = new URL(route.request().url());
    const req = route.request();
    if (req.method() === "POST") {
      expect(pathname).toBe("/api/runs/baseline/regrade");
      expect(req.headers()["x-workbench-intent"]).toBe("local");
      expect(req.postDataJSON()).toEqual({ evidence_version: "original" });
      regrades++;
      return route.fulfill({ json: runDetail });
    }
    if (pathname === "/api/runs/baseline") {
      if (regrades) readsAfterWrite++;
      return route.fulfill({ json: { ...runDetail, baseline_id: "first", report_versions: regrades ? [...runDetail.report_versions, "r3"] : runDetail.report_versions } });
    }
    if (pathname === "/api/runs/baseline/compare") {
      expect(searchParams.get("baseline_id")).toBe("first");
      return route.fulfill({ json: { baseline: initial, current: baseline, comparable: false, differences: [{ field: "model", baseline: "旧模型", current: "新模型" }] } });
    }
    return route.fulfill({ json: { items: [], has_more: false } });
  });
  await page.goto("/workbench.html?run_id=baseline");
  await page.getByRole("tab", { name: "问题与回归" }).click();
  await expect(page.getByText("旧模型", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "按此配置另起复跑" })).toHaveAttribute("href", /baseline_id=first/);
  await page.getByLabel("重判证据版本").selectOption("original");
  await page.getByRole("button", { name: "重判同一运行" }).click();
  await expect(page.getByRole("status").filter({ hasText: "已读取重判后的运行" })).toBeVisible();
  await expect(page.getByLabel("报告版本").locator("option[value=r3]")).toHaveCount(1);
  await expect(page).toHaveURL(/run_id=baseline/);
  expect(regrades).toBe(1);
  expect(readsAfterWrite).toBeGreaterThanOrEqual(1);
});

test("运行中打开的 Agent Trace 在执行结束后自动显示归档事件", async ({ page }) => {
  let completed = false;
  await page.route("**/api/**", async route => {
    const { pathname } = new URL(route.request().url());
    if (pathname === "/api/runs/baseline") {
      return route.fulfill({ json: { ...runDetail, run: { ...baseline, execution_status: completed ? "completed" : "running" } } });
    }
    if (pathname === "/api/runs/baseline/trace") {
      return route.fulfill({ json: { events: completed ? [traceEvent] : [], details: {} } });
    }
    return route.fulfill({ json: { items: [], has_more: false } });
  });
  await page.goto("/workbench.html?run_id=baseline");
  const inspector = page.getByRole("region", { name: baseline.title });
  await expect(inspector.getByText("执行中", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Agent 执行" }).click();
  await expect(page.getByText("此运行未归档 Agent Trace。", { exact: true })).toBeVisible();
  completed = true;
  await expect(inspector.getByText("已完成", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /首次写入/ })).toBeVisible();
});

test("窄屏翻页后返回同一档案分区，缺失 Trace 不补事件", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 850 });
  await page.route("**/api/**", async route => {
    const { pathname, searchParams } = new URL(route.request().url());
    if (pathname === "/api/runs/baseline/trace") return route.fulfill({ json: { events: [], details: {} } });
    if (pathname === "/api/runs/first") return route.fulfill({ json: { ...runDetail, run: initial } });
    if (pathname === "/api/runs/baseline") return route.fulfill({ json: runDetail });
    if (pathname === "/api/runs" && searchParams.get("timing") === "started") {
      if (searchParams.get("offset") === "20") return route.fulfill({ json: { items: [baseline], has_more: false } });
      return route.fulfill({ json: { items: [initial], has_more: true } });
    }
    return route.fulfill({ json: { items: [], has_more: false } });
  });
  await page.goto("/workbench.html");
  const archive = page.getByRole("region", { name: "执行时间顺序" });
  await archive.getByRole("button", { name: "下一页" }).click();
  await archive.getByRole("link", { name: /有效基线/ }).click();
  await expect(archive).toBeHidden();
  await page.getByRole("button", { name: "Agent 执行" }).click();
  await expect(page.getByText("此运行未归档 Agent Trace。", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "展开全部" })).toHaveCount(0);
  await page.getByRole("link", { name: "返回运行列表" }).click();
  await expect(archive).toContainText("第 2 页");
  await expect(archive.getByRole("link", { name: /有效基线/ })).toBeVisible();
});
