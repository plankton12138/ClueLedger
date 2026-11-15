import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect } from "./fixtures/frontendExperience";

test("按会话查看追踪，展开 Loop 查看失败与重试，窄屏切换到 Gate 拦截", async ({ page, book, request }) => {
  const root = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "state", "workspaces", book.id);
  mkdirSync(root, { recursive: true });
  function seed(conversation: string, title: string, records: Record<string, unknown>[]) {
    const started = Date.UTC(2026, 10, 8, 6, 32);
    const rows = [{ type: "conversation_meta", version: 2, title, created_at: new Date(started).toISOString() },
      ...records].map((row, seq) => ({
      seq, at: new Date(started + seq * 1000).toISOString(), conversation_id: conversation,
      ...(seq ? { event_id: `${conversation}:${seq}`, turn_id: "turn-1" } : {}), ...row,
    }));
    writeFileSync(resolve(root, `${conversation}.jsonl`), rows.map(row => JSON.stringify(row)).join("\n") + "\n");
  }
  seed("trace-check", "章节核对", [
    { type: "turn_started", trace_version: 1, text: "核对章节时间线", work_commit: "abcdef0123456789", story_revision: "" },
    { type: "work_revision_recorded", work_commit: "abcdef0123456789", story_revision: "" },
    { type: "loop_started", loop_id: "loop-one" },
    { type: "tool_started", loop_id: "loop-one", tool_call_id: "verify-1", tool: "核对时间线", args: { chapter: 3 } },
    { type: "tool_finished", loop_id: "loop-one", tool_call_id: "verify-1", ok: false, duration_ms: 1000, summary: "等待索引响应超时" },
    { type: "tool_started", loop_id: "loop-one", tool_call_id: "verify-2", retry_of_tool_call_id: "verify-1", tool: "重试核对" },
    { type: "tool_finished", loop_id: "loop-one", tool_call_id: "verify-2", ok: true, duration_ms: 1000 },
    { type: "model_request_started", loop_id: "loop-one", operation_id: "m-one", model: "test-model", stage: "write" },
    { type: "model_request_finished", loop_id: "loop-one", operation_id: "m-one", status: "completed", stop_reason: "stop", usage: { input: 10, output: 2 } },
    { type: "loop_finished", loop_id: "loop-one", status: "completed", duration_ms: 5000 },
    { type: "run_finished", status: "completed" },
  ]);
  seed("trace-gate", "正文修订", [
    { type: "turn_started", trace_version: 1, text: "写回正文", work_commit: "fedcba9876543210" },
    { type: "gate_decided", name: "写入资格", where: "tools.write", decision: "blocked", message: "等待作者确认" },
    { type: "run_finished", status: "needs_input" },
  ]);
  const base = `/api/file-workspaces/${book.id}/conversations`;
  expect((await request.get(`${base}/trace-check/turns/unknown/trace`)).status()).toBe(404);
  expect((await request.get(`/api/file-workspaces/${book.otherId}/conversations/trace-check/turns`)).status()).toBe(404);
  const trace = await (await request.get(`${base}/trace-check/turns/turn-1/trace`)).json();
  expect(trace.turn.work_commit).toBe("abcdef0123456789");

  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "活动记录", exact: true }).click();
  const panel = page.getByRole("dialog", { name: "活动记录" });
  await panel.getByRole("button", { name: "运行追踪", exact: true }).click();
  const session = panel.getByRole("combobox", { name: "会话", exact: true });
  await session.selectOption("trace-check");
  await expect(panel.getByText(/作品 abcdef0/)).toBeVisible();
  const loop = panel.getByRole("button", { name: /Loop 1.*已恢复/ });
  await expect(loop).toHaveAttribute("aria-expanded", "false");
  await expect(panel.getByRole("button", { name: /Tool call.*失败/ })).toHaveCount(0);
  await loop.press("Enter");
  await panel.getByRole("button", { name: /Tool call.*核对时间线.*失败/ }).click();
  await expect(panel.getByRole("heading", { name: "事件", exact: true })).toBeVisible();
  await expect(panel.locator(".trace-error")).toHaveText("等待索引响应超时");
  await expect(panel.getByRole("heading", { name: "输入", exact: true })).toBeVisible();
  await expect(panel.locator("pre")).toContainText('"chapter": 3');
  await panel.getByRole("button", { name: /Model.*test-model/ }).click();
  await expect(panel.getByText(/未保存模型输入|没有输入附件|正在确认是否保存/)).toBeVisible();
  await panel.getByRole("button", { name: "折叠全部" }).click();
  await expect(loop).toHaveAttribute("aria-expanded", "false");

  await page.setViewportSize({ width: 390, height: 720 });
  await session.selectOption("trace-gate");
  await panel.getByRole("button", { name: /Gate.*写入资格.*拦截/ }).click();
  await expect(panel.getByRole("heading", { name: "事件", exact: true })).toBeVisible();
  await expect(panel.locator(".trace-error")).toHaveText("等待作者确认");
  expect(await panel.locator(".trace-workbench").evaluate(element => element.scrollWidth)).toBeLessThanOrEqual(390);
});
