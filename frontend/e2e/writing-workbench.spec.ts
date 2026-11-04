import { test, expect, type Page } from "@playwright/test";
import { mkdirSync, mkdtempSync } from "node:fs";
import { resolve, join } from "node:path";

const screenshotDir = resolve(
  process.env.HASTINGS_E2E_SCREENSHOT_DIR ?? "../dev-notes/v1.2/artifacts/writing",
);

async function shot(page: Page, name: string) {
  mkdirSync(screenshotDir, { recursive: true });
  await page.screenshot({ path: join(screenshotDir, `${name}.png`) });
}

test("空作品只有对话可创作，章节正文以只读稿纸呈现", async ({ page, request }) => {
  const parent = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "books");
  mkdirSync(parent, { recursive: true });
  const root = join(mkdtempSync(join(parent, "writing-workbench-")), "作品");
  const opened = await request.post("/api/file-workspaces", {
    data: { path: root, create: true },
  });
  expect(opened.ok(), await opened.text()).toBeTruthy();
  const book = await opened.json();
  const base = `/api/file-workspaces/${book.id}`;

  await page.goto(`/#/workspaces/${book.id}/writing`);
  const chat = page.locator(".chat-input");
  await expect(chat).toBeVisible();
  await expect(chat).toBeFocused();
  await expect(page.getByRole("textbox", { name: "正文编辑" })).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "作品意图" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /新建章节|导入正文|添加模块/ })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "从一句话开始" })).toBeVisible();
  await expect(page.getByText("说说你想写什么。结构、章节和辅助资料由模型整理。", { exact: true })).toBeVisible();
  const starter = page.getByRole("button", { name: "“我还没想好，带我一步步想。”" });
  await starter.click();
  await expect(chat).toHaveValue("我还没想好，带我一步步想。");
  await expect(chat).toBeFocused();
  await page.getByRole("button", { name: "“我想写一个暴雪封山旅馆里的失踪案。”" }).click();
  await expect(chat).toHaveValue("我还没想好，带我一步步想。");
  const conversations = await request.get(`${base}/conversations`).then(response => response.json());
  expect(conversations.items).toEqual([]);
  await shot(page, "01-empty-paper-and-chat");

  // 章节接口仍供对话执行链复用，但写作页不暴露第二个创建或编辑入口。
  const created = await request.post(`${base}/story/chapters`, {
    data: {
      title: "雨夜",
      expected_plan_revision: "empty",
      request_key: "e2e-create-chapter",
    },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const chapter = await created.json();
  const saved = await request.put(`${base}/story/chapters/${chapter.chapter_id}/prose`, {
    data: {
      text: "# 雨夜\n\n侦探抵达旅馆。海面上没有灯。\n",
      expected_digest: chapter.prose_digest,
      draft_revision: "e2e-seed",
      request_key: "e2e-save-prose",
    },
  });
  expect(saved.ok(), await saved.text()).toBeTruthy();

  await page.reload();
  const chapterButton = page.locator(".writing-navigator").getByRole("button", { name: /第1章/ });
  await expect(chapterButton).toBeVisible();
  await chapterButton.click();
  await expect(page.getByText("侦探抵达旅馆。海面上没有灯。")).toBeVisible();
  await expect(page.getByRole("textbox", { name: "正文编辑" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /保存|续写|起草|方向建议/ })).toHaveCount(0);
  await shot(page, "02-readonly-chapter-and-chat");

  await page.setViewportSize({ width: 760, height: 800 });
  const chatButton = page.getByRole("button", { name: "对话", exact: true });
  await expect(chatButton).toBeVisible();
  await chatButton.click();
  await expect(page.locator(".chat-input")).toBeVisible();
  await expect(page.getByText("侦探抵达旅馆。海面上没有灯。")).toBeHidden();
});
