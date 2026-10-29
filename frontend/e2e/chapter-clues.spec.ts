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
test("本章包含早章线索；切换保留草稿，详情只有一处", async ({ page, book }) => {
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
  // 资料页已移除：详情只在这一处，不再有「全部线索」的第二份副本。
  await expect(page.getByRole("link", { name: "故事资料", exact: true })).toHaveCount(0);
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
  await page.getByRole("button", { name: "返回原处", exact: true }).click();
  await expect(page.locator(".paper-sheet")).toContainText("照片证明她看过背面");
  await expect(locate).toBeFocused();
  await expect(pane).toContainText("第 2 章 · 回收");
});
test("未整理、无发现、不足、空正文分别呈现，改稿后要求复核", async ({ page, book }) => {
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "本章线索", exact: true }).click();
  const pane = page.getByRole("tabpanel", { name: "本章线索" });
  for (const [title, text] of [["第三章 旁支", "本章还没有线索记录"], ["第四章 空结果", "已核对，未发现相关线索"], ["第五章 不足", "本章信息不足"], ["第六章 待写", "这一章还没有可读的正文"]]) {
    await selectChapter(page, title); await expect(pane).toContainText(text);
  }
  // 面板只读：整理任务入口已经随资料页移除。
  await expect(pane.getByRole("button", { name: /整理本章/ })).toHaveCount(0);
  await selectChapter(page, "第二章 照片");
  await pane.getByRole("button", { name: /信封背面的字/ }).click();
  writeFileSync(join(book.path, "正文", "0002 照片.md"), "# 照片\n\n改稿后照片不在现场。\n", "utf8");
  await pane.getByRole("button", { name: "重新读取", exact: true }).click();
  await expect(pane).toContainText("已有线索记录需要重新核对");
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
