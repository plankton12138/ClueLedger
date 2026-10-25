import { test, expect } from "./fixtures/frontendExperience";
import { mkdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page, Locator } from "@playwright/test";

async function openModels(page: Page) {
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("dialog", { name: "设置", exact: true }).getByRole("button", { name: "模型设置", exact: true }).click();
  return page.getByRole("dialog", { name: "模型设置", exact: true });
}
async function focusWithin(page: Page, dialog: Locator) {
  for (let n = 0; n < 12; n++) {
    await page.keyboard.press("Tab"); expect(await dialog.evaluate(el => el.contains(document.activeElement))).toBe(true);
  }
}
async function shot(page: Page, name: string) {
  const output = resolve("../dev-notes/v0.8/artifacts/frontend-experience/screenshots");
  mkdirSync(output, { recursive: true }); await page.screenshot({ path: `${output}/H5-${name}.png` });
}

test("目录选择取消不换作品，失败保留位置及错误，键盘焦点留在浮层", async ({ page, book }) => {
  let changes = 0;
  await page.route("**/api/file-workspaces/pick-directory", route => route.fulfill({ json: { path: null } }));
  await page.route("**/api/file-workspaces", async route => {
    if (route.request().method() !== "POST") return route.continue();
    changes++; await route.fulfill({ status: 400, json: { error: { code: "WORKSPACE_OPEN_FAILED", where: "作品目录", why: "目录不可用", fix: "选择可读取的作品目录" } } });
  });
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "作品", exact: true }).click();
  await page.getByRole("button", { name: "打开 / 新建", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "作品位置", exact: true });
  await dialog.getByRole("tab", { name: "打开已有", exact: true }).click();
  await dialog.getByRole("button", { name: "选择作品文件夹…", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "打开", exact: true })).toBeDisabled();
  await expect(page.getByLabel("选择作品目录")).toHaveValue(book.id); expect(changes).toBe(0);
  await dialog.getByRole("button", { name: new RegExp(`^空作品 `) }).last().click();
  await dialog.getByRole("button", { name: "打开", exact: true }).click();
  await expect(dialog.getByRole("alert")).toHaveText("[WORKSPACE_OPEN_FAILED] 作品目录：目录不可用。建议：选择可读取的作品目录。");
  await expect(dialog).toContainText("空作品"); await expect(dialog).toBeVisible();
  await expect(page.getByLabel("选择作品目录")).toHaveValue(book.id); expect(changes).toBe(1);
  await focusWithin(page, dialog); await shot(page, "directory-error-1440");
  await page.keyboard.press("Escape"); await expect(dialog).toBeHidden();
  await expect(page.getByRole("button", { name: "作品", exact: true })).toBeFocused();
});

test("模型连接临时凭据关闭即清空，不进入浏览器存储；失败保留普通输入", async ({ page, book }) => {
  // 仅使用测试哨兵，不读取 .env 或真实凭据，也不记录凭据输入后的截图。
  await page.goto(`/#/workspaces/${book.id}/writing`);
  const dialog = await openModels(page);
  await dialog.getByRole("button", { name: "新增连接", exact: true }).click();
  await dialog.getByRole("button", { name: /DeepSeek/ }).click();
  const key = dialog.getByLabel("API Key", { exact: true });
  await key.fill("synthetic-credential-for-lifecycle-test");
  await dialog.getByLabel("连接名称", { exact: true }).fill("测试临时连接");
  await focusWithin(page, dialog);
  expect(await page.evaluate(() => JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)]))).not.toContain("synthetic-credential");
  await page.keyboard.press("Escape"); await expect(dialog).toBeHidden();
  await expect(page.getByRole("button", { name: "设置", exact: true })).toBeFocused();
  await openModels(page);
  await dialog.getByRole("button", { name: "新增连接", exact: true }).click();
  await dialog.getByRole("button", { name: /DeepSeek/ }).click();
  await expect(key).toHaveValue("");
  await dialog.getByLabel("连接名称", { exact: true }).fill("保留名称输入");
  await dialog.getByRole("button", { name: "下一步：选择模型", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("[MODEL_KEY_REQUIRED]");
  await expect(dialog.getByLabel("连接名称", { exact: true })).toHaveValue("保留名称输入");
  await shot(page, "model-form-cleared-1440");
});

test("设置与活动窗口矩阵，文件版本、导出和维护入口可达", async ({ page, book }) => {
  await page.goto(`/#/workspaces/${book.id}/writing`);
  for (const width of [1440, 1024, 760, 640]) for (const scale of [100, 150]) {
    await page.setViewportSize({ width, height: width === 1024 ? 768 : width === 640 ? 800 : 900 });
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await page.getByRole("dialog", { name: "设置", exact: true }).getByLabel("调整字体大小").selectOption(String(scale));
    await page.keyboard.press("Escape");
    const models = await openModels(page); await expect(models.getByRole("button", { name: "新增连接", exact: true })).toBeInViewport();
    await shot(page, `settings-${width}-${scale}`); await page.keyboard.press("Escape");
    const trigger = page.getByRole("button", { name: "活动记录", exact: true }); await trigger.click();
    const activity = page.getByRole("dialog", { name: "活动记录", exact: true });
    await expect(activity.getByRole("button", { name: "导出事件日志", exact: true })).toBeInViewport();
    await focusWithin(page, activity);
    expect(await activity.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    const firstRow = activity.locator("main article > button").first();
    await expect(firstRow).toBeVisible();
    if (width === 1440 && scale === 100) {
      const pending = page.waitForEvent("download");
      await activity.getByRole("button", { name: "导出事件日志", exact: true }).click();
      const download = await pending;
      expect(download.suggestedFilename()).toBe("activity.jsonl");
      expect(readFileSync((await download.path())!, "utf8")).toContain('"operation"');
    }
    expect(await firstRow.evaluate(el => {
      const cells = [...el.children].map(child => child.getBoundingClientRect());
      return cells.every((cell, n) => !n || cell.left >= cells[n - 1].right);
    })).toBe(true);
    await activity.locator("aside").evaluate(el => { el.scrollTop = 0; });
    await shot(page, `activity-${width}-${scale}`);
    await activity.getByRole("button", { name: "文件版本", exact: true }).click();
    await expect(activity.getByText("未记录的改动", { exact: false })).toBeVisible();
    await activity.getByRole("button", { name: "事件流", exact: true }).click();
    await expect(activity.getByRole("button", { name: "确认清理详情", exact: true })).toHaveCount(0);
    await activity.getByText("维护工具", { exact: true }).click();
    await expect(activity.getByRole("button", { name: "清理已保存详情", exact: true })).toBeVisible();
    await page.keyboard.press("Escape"); await expect(activity).toBeHidden(); await expect(trigger).toBeFocused();
  }
});

test("宽阅读视图保留同一回复，复制失败提供原文，关闭返回入口", async ({ page, book }) => {
  const base = `**/api/file-workspaces/${book.id}`;
  await page.route(`${base}/conversation-ui`, route => route.fulfill({ json: { recent_id: "c1", drafts: {}, revision: "r1" } }));
  await page.route(`${base}/conversations`, route => route.fulfill({ json: { items: [{ id: "c1", title: "阅读夹具", created_at: "", updated_at: "" }], recent_id: "c1", active_turn: null, revision: "r1" } }));
  const text = "# 雨夜回复\n\n侦探在港口核对了潮汐表。";
  await page.route(`${base}/conversations/c1/messages?*`, route => route.fulfill({ json: { events: [
    { seq: 1, at: "", type: "turn_started", turn_id: "t1", text: "核对潮汐" },
    { seq: 2, at: "", type: "assistant_message", turn_id: "t1", text },
    { seq: 3, at: "", type: "run_finished", turn_id: "t1", status: "completed", recording: "no_change", changed_paths: [] },
  ], last_seq: 3, active_turn: null, revision: "r1" } }));
  await page.addInitScript(() => Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: () => Promise.reject(new Error("blocked")) } }));
  await page.goto(`/#/workspaces/${book.id}/writing`);
  const trigger = page.getByRole("button", { name: "展开阅读", exact: true }); await trigger.click();
  const dialog = page.getByRole("dialog", { name: "阅读这条回复", exact: true });
  await expect(dialog).toContainText("侦探在港口核对了潮汐表。");
  await focusWithin(page, dialog); await page.keyboard.press("Escape"); await expect(trigger).toBeFocused();
  await page.getByRole("button", { name: "复制", exact: true }).click();
  await expect(dialog.getByLabel("回复原文", { exact: true })).toHaveText(text);
  await shot(page, "reply-copy-fallback-1440");
  await page.keyboard.press("Escape"); await expect(dialog).toBeHidden();
  await expect(page.getByRole("button", { name: "复制", exact: true })).toBeFocused();
});
