import { test, expect } from "./fixtures/frontendExperience";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const QUOTE = "照片证明她看过背面。";

test.beforeEach(async ({ book }) => {
  const root = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e");
  execFileSync("uv", ["run", "--directory", "..", "python", resolve("e2e/fixtures/clue_review.py"), book.path, join(root, "state", "workspaces", book.id)], { windowsHide: true, stdio: "pipe" });
});

async function openClue(page: import("@playwright/test").Page, chapter: string, clue: string) {
  await page.getByRole("button", { name: chapter, exact: true, includeHidden: true }).click();
  await page.getByRole("button", { name: "本章线索", exact: true }).click();
  const pane = page.getByRole("tabpanel", { name: "本章线索" });
  await pane.getByRole("button", { name: new RegExp(clue) }).click();
  return pane;
}

test("核对报告显示范围、来源状态与具体问题，并能定位原文", async ({ page, book }) => {
  await page.goto(`/#/workspaces/${book.id}/writing`);
  const pane = await openClue(page, "第二章 照片", "公开的照片");
  await expect(pane).toContainText("上次核对：发现问题");
  await pane.getByRole("button", { name: "查看核对报告", exact: true }).click();

  const report = pane.getByRole("region", { name: "核对报告" });
  await expect(report).toBeVisible();
  await expect(report).toContainText("林岚提前接触过遗书原件");
  await expect(report).toContainText("发现问题");
  await expect(report).toContainText("核对截至第 2 章");
  await expect(report).toContainText("依据有效");
  await expect(report).toContainText("反证");
  await expect(report).toContainText("正文支持");
  await expect(report).toContainText("林岚说没见过信封，但照片显示她看过背面。");
  await expect(report).toContainText("建议：补一句她是否记得这封信。");
  await expect(report.locator("blockquote")).toHaveText([QUOTE]);
  await report.locator("summary").filter({ hasText: "核对范围" }).click();
  await expect(report).toContainText("已读全文");
  const shots = resolve("../dev-notes/v1.0/artifacts/s5");
  mkdirSync(shots, { recursive: true });
  await page.screenshot({ path: join(shots, "clue-review-1440.png") });

  await report.getByRole("button", { name: "定位原文", exact: true }).click();
  await expect(page.locator(".writing-paper mark[data-source-target]")).toHaveText(QUOTE);
  await page.getByRole("button", { name: "返回原处", exact: true }).click();
  await expect(report.getByRole("button", { name: "定位原文", exact: true })).toBeFocused();
});

test("正文改稿后旧报告保留并标出需复核", async ({ page, book }) => {
  await page.goto(`/#/workspaces/${book.id}/writing`);
  const pane = await openClue(page, "第二章 照片", "公开的照片");
  await pane.getByRole("button", { name: "查看核对报告", exact: true }).click();
  const report = pane.getByRole("region", { name: "核对报告" });
  await expect(report).toContainText("依据有效");

  writeFileSync(join(book.path, "正文", "0002 照片.md"), "# 照片\n\n改稿后照片不在现场。\n", "utf8");
  await pane.getByRole("button", { name: "收起报告", exact: true }).click();
  await pane.getByRole("button", { name: "重新读取", exact: true }).click();
  await pane.getByRole("button", { name: "查看核对报告", exact: true }).click();
  await expect(report).toContainText("正文已修改，需复核");
  await expect(report).toContainText("正文已修改：正文/0002 照片.md");
  await expect(report.locator("blockquote")).toHaveText([QUOTE]);
  await expect(pane).toContainText("上次核对：发现问题");
});

test("窄屏与放大字号下报告单区域可读", async ({ page, book }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "目录", exact: true }).click();
  const pane = await openClue(page, "第二章 照片", "公开的照片");
  await pane.getByRole("button", { name: "查看核对报告", exact: true }).click();
  const report = pane.getByRole("region", { name: "核对报告" });
  await expect(report).toBeVisible();
  await expect(report).toContainText("反证");
  await expect(page.locator(".writing-chat")).toBeVisible();
  const shots = resolve("../dev-notes/v1.0/artifacts/s5");
  mkdirSync(shots, { recursive: true });
  await page.screenshot({ path: join(shots, "clue-review-390.png") });
});
