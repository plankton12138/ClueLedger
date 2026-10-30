import { test, expect } from "./fixtures/frontendExperience";
import type { Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

async function prepare(page: Page, workspace: string) {
  const prefix = `**/api/file-workspaces/${workspace}`;
  const at = "2026-10-27T16:00:00+08:00";
  const ids = ["c_usage_a", "c_usage_b"];
  const items = ids.map((id, i) => ({ id, title: `用量会话${i + 1}`, created_at: at, updated_at: at }));
  let operation: Record<string, unknown> | null = null;
  let stops = 0;
  let submissions = 0;
  await page.route(`${prefix}/conversations`, route => route.fulfill({ json: { items, recent_id: ids[0], active_turn: null, revision: "r1" } }));
  await page.route(`${prefix}/conversation-ui`, route => route.fulfill({ json: { recent_id: ids[0], drafts: {}, revision: "r1" } }));
  for (const [i, id] of ids.entries()) {
    await page.route(`${prefix}/conversations/${id}/messages?*`, route => route.fulfill({ json: {
      events: [{ seq: 1, at, type: "turn_started", turn_id: "t1", text: items[i].title },
        { seq: 2, at, type: "run_finished", turn_id: "t1", status: "completed", changed_paths: [], recording: "no_change" }],
      last_seq: 2, active_turn: null, revision: "r1",
    } }));
    await page.route(`${prefix}/conversations/${id}/usage`, route => route.fulfill({ json: {
      conversation_id: id, through_seq: i === 0 ? 100 : 2, revision: "r1", diagnostics: [], compaction: i === 0 ? operation : null,
      context: { status: "ready", estimated_active_tokens: i === 0 ? 52000 : 9000, context_window_tokens: i === 0 ? 64000 : null,
        input_limit_tokens: 56000, output_reserve_tokens: 4096, budget: i === 0 ? "near" : "unknown", instructions_tokens: 1000,
        tools_tokens: 500, conversation_tokens: i === 0 ? 50500 : 7500, method: "cjk-aware-v1", fingerprint: id, limits_revision: "r1", last_request_input_tokens: 52000 },
      usage: { total_tokens: i === 0 ? 54800 : null, input_tokens: i === 0 ? 54000 : null, output_tokens: i === 0 ? 800 : null,
        cache_read_tokens: 0, cache_write_tokens: 0, finished_requests: 1, requests_without_usage: i === 0 ? 0 : 1, requests_in_progress: 0, model_duration_ms: 900 },
    } }));
  }
  await page.route(`${prefix}/conversations/${ids[0]}/compact`, async route => {
    submissions++;
    operation = { operation_id: "op_usage", conversation_id: ids[0], state: "running", record: null, error: null, note: "" };
    await route.fulfill({ status: 202, json: operation });
  });
  await page.route(`${prefix}/conversations/${ids[0]}/compactions/op_usage`, route => route.fulfill({ json: operation }));
  await page.route(`${prefix}/conversations/${ids[0]}/compactions/op_usage/stop`, async route => {
    stops++; operation = { ...operation, state: "stopped", note: "压缩已取消；当前上下文没有变化。" };
    await route.fulfill({ json: operation });
  });
  const open = async () => {
    await page.goto(`/#/workspaces/${workspace}/writing`);
    const entry = page.getByRole("button", { name: "当前上下文占用" });
    await expect(page.getByLabel("选择对话")).toBeAttached();
    if (!(await entry.isVisible())) await page.getByRole("button", { name: "对话", exact: true }).click();
    await expect(entry).toContainText("81%");
  };
  return { ids, open, counts: () => ({ stops, submissions }) };
}

test("用量按会话隔离，低游标及延迟旧响应正常显示", async ({ page, book }) => {
  const fixture = await prepare(page, book.id);
  await fixture.open();
  let release: () => void = () => undefined;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let waiting = false;
  await page.route(`**/conversations/${fixture.ids[0]}/usage`, async route => { waiting = true; await gate; await route.fallback(); });
  await page.reload();
  await expect.poll(() => waiting).toBe(true);
  await page.getByLabel("选择对话").selectOption(fixture.ids[1]);
  await expect(page.getByRole("button", { name: "当前上下文占用" })).toContainText("≈9000");
  await expect(page.getByRole("button", { name: "本次对话累计用量" })).toContainText("未知");
  release(); await page.waitForTimeout(400);
  await expect(page.getByRole("button", { name: "当前上下文占用" })).toContainText("≈9000");
});

test("压缩可停止，刷新和切回会话后恢复同一操作", async ({ page, book }) => {
  const fixture = await prepare(page, book.id);
  await fixture.open();
  const entry = page.getByRole("button", { name: "当前上下文占用" });
  await entry.click(); await page.getByRole("button", { name: "压缩上下文", exact: true }).click();
  await expect(page.getByRole("button", { name: "停止压缩", exact: true })).toBeVisible();
  await page.getByLabel("选择对话").selectOption(fixture.ids[1]);
  await page.getByLabel("选择对话").selectOption(fixture.ids[0]);
  await entry.click(); await expect(page.getByRole("button", { name: "停止压缩", exact: true })).toBeVisible();
  await page.reload(); await entry.click(); await page.getByRole("button", { name: "停止压缩", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("压缩已取消");
  expect(fixture.counts()).toEqual({ stops: 1, submissions: 1 });
});

test("浮层互斥、键盘及窄屏缩放可用，不遮挡草稿", async ({ page, book }) => {
  const fixture = await prepare(page, book.id);
  await fixture.open();
  const output = resolve(process.env.HASTINGS_E2E_SCREENSHOT_DIR ?? "../data/test-tmp/context-fixes"); mkdirSync(output, { recursive: true });
  for (const [width, height, scale] of [[1440, 900, 1], [1024, 768, 1], [640, 900, 1], [320, 720, 1], [1440, 900, 1.5]]) {
    await page.setViewportSize({ width, height });
    await page.evaluate(value => { document.documentElement.style.zoom = String(value); }, scale);
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    const entry = page.getByRole("button", { name: "当前上下文占用" });
    if (!(await entry.isVisible())) await page.getByRole("button", { name: "对话", exact: true }).click();
    await entry.focus(); await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog", { name: "当前上下文", exact: true }); await expect(dialog).toBeVisible();
    const pop = await dialog.boundingBox(); const input = await page.locator(".chat-input").boundingBox();
    expect(pop!.x).toBeGreaterThanOrEqual(0); expect(pop!.x + pop!.width).toBeLessThanOrEqual(width + 1);
    expect(pop!.y).toBeGreaterThanOrEqual(0); expect(pop!.y + pop!.height).toBeLessThanOrEqual(input!.y);
    await page.screenshot({ path: resolve(output, `${width}-${scale}.png`) });
    await page.keyboard.press("Escape"); await expect(entry).toBeFocused();
  }
  await page.evaluate(() => { document.documentElement.style.zoom = "1"; });
  await page.getByRole("button", { name: "当前上下文占用" }).click();
  await page.getByRole("button", { name: "本次对话累计用量" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(1);
  await page.locator(".chat-input").click(); await expect(page.getByRole("dialog")).toHaveCount(0);
});
