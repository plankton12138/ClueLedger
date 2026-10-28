import { test, expect } from "./fixtures/frontendExperience";
import { execFileSync } from "node:child_process";
import { join, resolve } from "node:path";
import { mkdirSync, writeFileSync } from "node:fs";

test.beforeEach(async ({ book }) => {
  const root = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e");
  execFileSync("uv", ["run", "--directory", "..", "python", resolve("e2e/fixtures/chapter_clues.py"), book.path, join(root, "state", "workspaces", book.id)], { windowsHide: true, stdio: "pipe" });
});
async function selectChapter(page: import("@playwright/test").Page, title: string) {
  const button = page.getByRole("button", { name: title, exact: true, includeHidden: true });
  await expect(button).toBeAttached();
  if (!await button.isVisible()) await page.getByRole("button", { name: "目录", exact: true }).click();
  await button.click();
}
test("本章包含早章线索；切换保留草稿，资料页共用一份详情", async ({ page, book }) => {
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await selectChapter(page, "第二章 照片");
  const draft = "请保留这个未发送的修改要求。".repeat(20);
  await page.locator(".chat-input").fill(draft);
  await page.getByRole("button", { name: "本章线索", exact: true }).click();
  const pane = page.getByRole("tabpanel", { name: "本章线索" });
  await pane.getByRole("button", { name: /信封背面的字/ }).click();
  await expect(pane.getByRole("heading", { name: "信封背面的字", exact: true })).toHaveCount(1);
  await expect(pane.getByRole("heading", { name: "正文依据", exact: true })).toHaveCount(1);
  await expect(pane).toContainText("第 1 章 · 埋设");
  await expect(pane).toContainText("第 2 章 · 再次提及");
  await expect(pane).toContainText("证词");
  await page.getByRole("tab", { name: "对话", exact: true }).click();
  await expect(page.locator(".chat-input")).toHaveValue(draft);
  await page.getByRole("tab", { name: "本章线索", exact: true }).click();
  await pane.getByRole("button", { name: "全部线索", exact: true }).click();
  await expect(page.locator(".materials-evidence .clue-detail")).toHaveCount(1);
  await expect(page.locator(".materials-evidence").getByRole("heading", { name: "正文依据", exact: true })).toHaveCount(1);
  await expect(page.locator(".materials-evidence blockquote").first()).toHaveText("信封背面有字。");
});
test("定位早章后保持本章范围，返回恢复详情、原文件与焦点", async ({ page, book }) => {
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await selectChapter(page, "第二章 照片");
  await page.getByRole("button", { name: "本章线索", exact: true }).click();
  const pane = page.getByRole("tabpanel", { name: "本章线索" });
  await pane.getByRole("button", { name: /信封背面的字/ }).click();
  const locate = pane.getByRole("button", { name: "定位原文", exact: true }).first(); await locate.click();
  await expect(page.locator(".writing-paper mark[data-source-target]")).toHaveText("信封背面有字。");
  await expect(pane).toContainText("第二章 照片");
  await expect(page.getByLabel("原文定位")).toContainText("任务截止章仍为第 2 章");
  await page.getByRole("button", { name: "关闭定位", exact: true }).click();
  await expect(page.locator(".paper-sheet")).toContainText("照片证明她看过背面");
  await expect(locate).toBeFocused();
  await expect(pane).toContainText("第 2 章 · 回收");
});
test("未整理、无发现、不足、空正文分别呈现，改稿后要求复核", async ({ page, book }) => {
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "本章线索", exact: true }).click();
  const pane = page.getByRole("tabpanel", { name: "本章线索" });
  for (const [title, text] of [["第三章 旁支", "本章尚未整理"], ["第四章 空结果", "已整理，未发现相关线索"], ["第五章 不足", "本章信息不足"], ["第六章 待写", "这一章还没有可整理的正文"]]) {
    await selectChapter(page, title); await expect(pane).toContainText(text);
  }
  await expect(pane.getByRole("button", { name: /整理本章/ })).toBeDisabled();
  await selectChapter(page, "第二章 照片");
  await pane.getByRole("button", { name: /信封背面的字/ }).click();
  writeFileSync(join(book.path, "正文", "0002 照片.md"), "# 照片\n\n改稿后照片不在现场。\n", "utf8");
  await pane.getByRole("button", { name: "重新读取", exact: true }).click();
  await expect(pane).toContainText("已有整理结果需要重新核对");
  await expect(pane).toContainText("回收待复核");
  await expect(pane).toContainText("来源已修改");
  await expect(pane.locator("blockquote")).toContainText(["信封背面有字。", "林岚说她没看过信封。", "照片证明她看过背面。"]);
});
test("窄屏、放大字号与键盘切换使用单区域并保留草稿", async ({ page, book }) => {
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await selectChapter(page, "第二章 照片");
  await page.locator(".chat-input").fill("窄屏草稿仍在。");
  await page.getByRole("button", { name: "本章线索", exact: true }).click();
  const pane = page.getByRole("tabpanel", { name: "本章线索" });
  await pane.getByRole("button", { name: /信封背面的字/ }).click();
  const output = resolve("../data/test-tmp/chapter-clues"); mkdirSync(output, { recursive: true });
  await page.screenshot({ path: join(output, "chapter-clues-1440.png") });
  for (const width of [1440, 1024, 640, 390]) {
    await page.setViewportSize({ width, height: 900 });
    if (!await pane.isVisible()) await page.getByRole("button", { name: "本章线索", exact: true }).click();
    await expect(pane).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("combobox", { name: "调整字体大小" }).selectOption("150");
  await page.keyboard.press("Escape");
  await expect(pane).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: join(output, "chapter-clues-390-150.png") });
  await page.getByRole("tab", { name: "本章线索", exact: true }).focus();
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByRole("tab", { name: "对话", exact: true })).toBeFocused();
  await expect(page.locator(".chat-input")).toHaveValue("窄屏草稿仍在。");
  await page.keyboard.press("ArrowRight"); await page.keyboard.press("Escape");
  await expect(page.getByRole("region", { name: "正文阅读", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "本章线索", exact: true })).toBeFocused();
});

test("整理本章提交实际范围与指纹；受理冲突保留草稿和既有记录", async ({ page, book, request }) => {
  const chapterPath = "正文/0002 照片.md";
  const view = await request.get(`/api/file-workspaces/${book.id}/clues?chapter_path=${encodeURIComponent(chapterPath)}&as_of_chapter=2`).then(response => response.json());
  const attempts: any[] = [];
  let submitted: any;
  // 只控制受理冲突，正文、覆盖、列表和详情仍使用实际服务。
  await page.route(`**/api/file-workspaces/${book.id}/material-tasks`, async route => {
    if (route.request().method() !== "POST") return route.continue();
    submitted = route.request().postDataJSON();
    attempts.push(submitted);
    if (attempts.length === 1) return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: { code: "MATERIAL_UNAVAILABLE", why: "受理响应暂不可读", fix: "保留请求并重试" } }) });
    await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: { code: "CONTEXT_SOURCE_STALE_CONFLICT", why: "目标原文已变化", fix: "重新读取后整理" } }) });
  });
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await selectChapter(page, "第二章 照片");
  await page.locator(".chat-input").fill("尚未发送的作者要求。");
  await page.getByRole("button", { name: "本章线索", exact: true }).click();
  const pane = page.getByRole("tabpanel", { name: "本章线索" });
  await expect(pane.getByRole("button", { name: /信封背面的字/ })).toBeVisible();
  await pane.getByRole("button", { name: "重新整理本章", exact: true }).click();
  await expect(pane).toContainText("[MATERIAL_UNAVAILABLE]");
  await expect(pane.getByRole("button", { name: "重新整理本章", exact: true })).toBeEnabled();
  await pane.getByRole("button", { name: "重新整理本章", exact: true }).click();
  await expect(pane).toContainText("[CONTEXT_SOURCE_STALE_CONFLICT]");
  expect(attempts).toHaveLength(2);
  expect(attempts[0].request_key).toBe(attempts[1].request_key);
  expect(submitted.scope).toMatchObject({ domain: "clues", chapter_paths: [chapterPath], entity_paths: [], as_of_chapter: 2, observed_digests: { [chapterPath]: view.chapter.digest } });
  expect(submitted.request_key).toBeTruthy();
  await expect(pane.getByRole("button", { name: /信封背面的字/ })).toBeVisible();
  await page.getByRole("tab", { name: "对话", exact: true }).click();
  await expect(page.locator(".chat-input")).toHaveValue("尚未发送的作者要求。");
});
