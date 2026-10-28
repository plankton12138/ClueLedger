import { test, expect } from "./fixtures/frontendExperience";
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { join, resolve } from "node:path";

const QUOTE = "照片证明她看过背面。";

test.beforeEach(async ({ book }) => {
  const root = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e");
  execFileSync("uv", ["run", "--directory", "..", "python", resolve("e2e/fixtures/clue_review.py"), book.path, join(root, "state", "workspaces", book.id)], { windowsHide: true, stdio: "pipe" });
});

async function openChapter(page: import("@playwright/test").Page, title: string) {
  const button = page.getByRole("button", { name: title, exact: true, includeHidden: true });
  await expect(button).toBeAttached();
  if (!await button.isVisible()) await page.getByRole("button", { name: "目录", exact: true }).click();
  await button.click();
}

async function openPhotoClue(page: import("@playwright/test").Page) {
  await openChapter(page, "第二章 照片");
  await page.getByRole("button", { name: "本章线索", exact: true }).click();
  const pane = page.getByRole("tabpanel", { name: "本章线索" });
  await pane.getByRole("button", { name: /公开的照片/ }).click();
  return pane;
}

test("写作者从线索详情走完核对、定位、带入对话，刷新后仍在", async ({ page, book }) => {
  await page.goto(`/#/workspaces/${book.id}/writing`);
  const pane = await openPhotoClue(page);
  await expect(pane).toContainText("上次核对：发现问题");

  await pane.getByRole("button", { name: "查看核对报告", exact: true }).click();
  const report = pane.getByRole("region", { name: "核对报告" });
  await expect(report).toContainText("林岚提前接触过遗书原件");
  await report.getByRole("button", { name: "定位原文", exact: true }).click();
  await expect(page.locator(".writing-paper mark[data-source-target]")).toHaveText(QUOTE);
  await page.getByRole("button", { name: "关闭定位", exact: true }).click();

  await pane.getByRole("button", { name: "带入对话", exact: true }).click();
  const chips = page.locator('[aria-label="引用线索"]');
  await expect(chips).toContainText("公开的照片");
  await expect(page.locator(".chat-input")).toHaveValue("");

  const shots = resolve("../dev-notes/v1.0/artifacts/s7");
  mkdirSync(shots, { recursive: true });
  await page.screenshot({ path: join(shots, "clue-workflow-1440.png") });

  await page.reload();
  await expect(page.locator('[aria-label="引用线索"]')).toContainText("公开的照片");
  const again = await openPhotoClue(page);
  await again.getByRole("button", { name: "查看核对报告", exact: true }).click();
  await expect(again.getByRole("region", { name: "核对报告" })).toContainText(QUOTE);
});

test("切到更早章节后，后章报告与依据不再出现在本章区域", async ({ page, book }) => {
  await page.goto(`/#/workspaces/${book.id}/writing`);
  const pane = await openPhotoClue(page);
  await pane.getByRole("button", { name: "查看核对报告", exact: true }).click();
  await expect(pane.getByRole("region", { name: "核对报告" })).toContainText("核对截至第 2 章");

  await openChapter(page, "第一章 遗书");
  const early = page.getByRole("tabpanel", { name: "本章线索" });
  if (await early.count() === 0 || !await early.isVisible()) {
    await page.getByRole("button", { name: "本章线索", exact: true }).click();
  }
  await expect(early).toContainText("查看截至第 1 章");
  await expect(early.getByRole("region", { name: "核对报告" })).toHaveCount(0);
  await expect(early).not.toContainText(QUOTE);
});

test("窄屏与放大字号下核对与引用仍可操作", async ({ page, book }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("combobox", { name: "调整字体大小" }).selectOption("150");
  await page.keyboard.press("Escape");

  const pane = await openPhotoClue(page);
  await pane.getByRole("button", { name: "查看核对报告", exact: true }).click();
  const report = pane.getByRole("region", { name: "核对报告" });
  await expect(report).toBeVisible();
  await pane.getByRole("button", { name: "收起报告", exact: true }).click();
  await pane.getByRole("button", { name: "带入对话", exact: true }).click();
  await expect(page.locator('[aria-label="引用线索"]')).toContainText("公开的照片");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

  const shots = resolve("../dev-notes/v1.0/artifacts/s7");
  mkdirSync(shots, { recursive: true });
  await page.screenshot({ path: join(shots, "clue-workflow-390.png") });
});
