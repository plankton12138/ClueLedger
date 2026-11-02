import { test, expect, type Page } from "@playwright/test";
import { mkdirSync, mkdtempSync } from "node:fs";
import { resolve, join } from "node:path";

const screenshotDir = resolve(process.env.HASTINGS_E2E_SCREENSHOT_DIR ?? "../dev-notes/v1.0.9/artifacts/aqua");

async function shot(page: Page, name: string) {
  mkdirSync(screenshotDir, { recursive: true });
  await page.screenshot({ path: join(screenshotDir, `${name}.png`) });
}

test("从空作品到第一章写作不离开写作页，刷新后正文仍在", async ({ page, request }) => {
  const parent = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "books");
  mkdirSync(parent, { recursive: true });
  const root = join(mkdtempSync(join(parent, "writing-workbench-")), "作品");
  const opened = await request.post("/api/file-workspaces", { data: { path: root, create: true } });
  expect(opened.ok(), await opened.text()).toBeTruthy();
  const book = await opened.json();

  await page.goto(`/#/workspaces/${book.id}/writing`);
  await expect(page.getByRole("heading", { name: "全书构思", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "下一步" })).toContainText("先写一句作品起点");
  await expect(page.locator(".writing-navigator").getByRole("button", { name: "先完成全书构思" })).toBeDisabled();
  await expect(page.getByRole("textbox", { name: "正文编辑" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "写作", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "构思", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "线索", exact: true })).toHaveCount(0);
  await shot(page, "05-book-guide-1440");

  await page.getByRole("textbox", { name: "作品意图" }).fill("暴雨封住了出山的公路，旅馆里少了一名住客。");
  await page.getByRole("button", { name: "保存作品意图", exact: true }).click();
  await expect(page.locator(".plan-actions").getByRole("status")).toHaveText("已保存");
  await expect(page.locator(".writing-navigator").getByRole("button", { name: "新建章节" })).toBeEnabled();
  await expect(page.getByRole("button", { name: /建立第一章/ })).toBeEnabled();
  await page.getByRole("button", { name: /建立第一章/ }).click();

  // 建章即建正文：没有章节构思页，直接进入可编辑稿纸。
  await expect(page).toHaveURL(new RegExp(`/workspaces/${book.id}/writing\\?chapter=ch_`));
  const editor = page.getByRole("textbox", { name: "正文编辑" });
  await expect(editor).toBeVisible();
  const emptyStart = page.getByRole("region", { name: "开始正文" });
  await expect(emptyStart).toContainText("正文还没有内容");
  await expect(emptyStart.getByRole("button", { name: "自己写第一段" })).toBeVisible();
  await expect(emptyStart.getByRole("button", { name: "由模型起草" })).toBeVisible();
  await expect(emptyStart.getByRole("button", { name: "看看方向建议" })).toBeVisible();
  // V03：空正文区只有一个主操作，模型入口不与它争夺层级。
  await expect(emptyStart.locator('button[data-variant="primary"]')).toHaveCount(1);
  await expect(emptyStart.getByRole("button", { name: "自己写第一段" })).toHaveAttribute("data-variant", "primary");
  await expect(emptyStart.getByRole("button", { name: "由模型起草" })).toHaveAttribute("data-variant", "secondary");
  await expect(editor).toHaveValue("");
  await shot(page, "06-empty-prose-1440");

  await editor.fill("侦探抵达港口。");
  const status = page.locator(".chapter-editor-status");
  await expect(status).toContainText("未保存");
  await expect(status).toContainText("已保存", { timeout: 15_000 });

  // 本章没有可核对的信息变化：温和提示出现，可忽略，不阻断继续输入。
  await expect(page.getByText("本章还没有可核对的信息变化")).toBeVisible();
  await shot(page, "07-empty-chapter-feedback-1440");
  await page.getByRole("button", { name: "暂时忽略" }).click();
  await expect(page.getByText("本章还没有可核对的信息变化")).toHaveCount(0);
  await editor.fill("侦探抵达港口。海面上没有灯。");
  await expect(status).toContainText("已保存", { timeout: 15_000 });

  await page.reload();
  await expect(page.getByRole("textbox", { name: "正文编辑" })).toHaveValue(/侦探抵达港口/);
  await page.locator(".writing-navigator").getByRole("button", { name: "全书构思" }).click();
  await expect(page.getByRole("heading", { name: "全书构思", exact: true })).toBeVisible();
  await expect(page.getByText("信息线从正文里长出来")).toBeVisible();
  await page.locator(".writing-navigator").getByRole("button", { name: /第1章/ }).click();
  await expect(page.getByRole("textbox", { name: "正文编辑" })).toHaveValue(/侦探抵达港口/);

  // 1024 宽度下索引是辅助区，正文仍可见；760 宽度下辅助区独占主内容，关闭后回到正文。
  await page.setViewportSize({ width: 1024, height: 800 });
  const chatButton = page.getByRole("button", { name: "对话", exact: true });
  await expect(chatButton).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator(".chapter-margin-inline")).toBeVisible();
  await expect(page.locator(".chapter-margin-inline")).toContainText("还没有可核对的信息变化");
  await expect(page.getByRole("button", { name: "本章信息", exact: true })).toHaveCount(0);
  await chatButton.click();
  await expect(page.locator(".chapter-margin-inline")).toBeHidden();
  await chatButton.click();
  await expect(page.locator(".chapter-margin-inline")).toBeVisible();

  await page.setViewportSize({ width: 760, height: 800 });
  await page.getByRole("button", { name: "本章信息", exact: true }).click();
  await expect(page.getByRole("region", { name: "本章信息" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "正文编辑" })).toBeHidden();
  await page.getByRole("button", { name: "本章信息", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "正文编辑" })).toHaveValue(/侦探抵达港口/);
});
