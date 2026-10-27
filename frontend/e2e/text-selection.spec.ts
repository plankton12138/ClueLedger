import { test, expect } from "./fixtures/frontendExperience";
import type { Locator } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

async function selectText(root: Locator, text: string) {
  await root.evaluate((node, wanted) => {
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    let current: Node | null;
    while ((current = walker.nextNode())) {
      const start = current.textContent?.indexOf(wanted) ?? -1;
      if (start < 0) continue;
      const range = document.createRange(); range.setStart(current, start); range.setEnd(current, start + wanted.length);
      window.getSelection()?.removeAllRanges(); window.getSelection()?.addRange(range);
      return;
    }
    throw new Error(`没有找到选段：${wanted}`);
  }, text);
}

test("正文与装载模块的选段独立保存，刷新后可查看和移除", async ({ page, request, book }) => {
  const detail = await request.get(`/api/module-library/modules/${book.moduleId}/revisions/${book.revisionId}`).then(r => r.json());
  const mounted = await request.put(`/api/file-workspaces/${book.id}/assembly`, { data: {
    bindings: [{ label: "港口资料", module: { library_id: detail.library_id, module_id: book.moduleId, revision_id: book.revisionId } }],
    expected_digest: null, request_key: crypto.randomUUID(),
  } });
  expect(mounted.ok(), await mounted.text()).toBeTruthy();
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "第一章 雨夜", exact: true }).click();
  const body = page.locator(".paper-sheet [data-text-selection-source]");
  await expect(body).toContainText("侦探抵达港口，雨仍未停。");
  await expect(page.getByRole("button", { name: "添加到对话", exact: true })).toHaveCount(0);
  await selectText(body, "侦探抵达港口，雨仍未停。");
  await page.getByRole("button", { name: "添加到对话", exact: true }).click();
  const quotes = page.locator(".text-reference-list");
  await expect(quotes).toContainText("侦探抵达港口，雨仍未停。");
  await expect(page.locator(".chat-input")).toBeFocused();
  await page.locator(".chat-input").fill("帮我核对这两段");
  await page.getByRole("button", { name: "港口", exact: true }).click();
  const module = page.getByRole("article", { name: "模块内容", exact: true });
  await expect(module).toContainText("潮汐表。");
  await selectText(module.locator("[data-text-selection-source]"), "潮汐表。");
  await page.getByRole("button", { name: "添加到对话", exact: true }).click();
  await expect(quotes.locator(".text-reference-chip")).toHaveCount(2);
  await expect.poll(async () => {
    const ui = await request.get(`/api/file-workspaces/${book.id}/conversation-ui`).then(r => r.json());
    return Object.values(ui.draft_contexts).flatMap((item: any) => item.text_references ?? []).length;
  }).toBe(2);
  await page.reload();
  await expect(quotes.locator(".text-reference-chip")).toHaveCount(2);
  await expect(page.locator(".chat-input")).toHaveValue("帮我核对这两段");
  await quotes.locator("summary").last().click();
  await expect(quotes.locator("blockquote").last()).toHaveText("潮汐表。");
  const output = resolve("../data/test-tmp/text-selection"); mkdirSync(output, { recursive: true });
  await page.screenshot({ path: resolve(output, "references-1440.png") });
  await quotes.getByRole("button", { name: /移除选段/ }).last().click();
  await expect(quotes.locator(".text-reference-chip")).toHaveCount(1);
});

test("窄屏添加选段打开对话，跨阅读区的选择不显示入口", async ({ page, book }) => {
  await page.setViewportSize({ width: 640, height: 900 });
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "目录", exact: true }).click();
  await page.getByRole("button", { name: "第一章 雨夜", exact: true }).click();
  const body = page.locator(".paper-sheet [data-text-selection-source]");
  await expect(body).toBeVisible();
  await selectText(body, "侦探抵达港口");
  const action = page.getByRole("button", { name: "添加到对话", exact: true });
  await expect(action).toBeInViewport();
  await page.keyboard.press("Escape"); await expect(action).toHaveCount(0);
  await body.evaluate(node => {
    const range = document.createRange();
    range.setStartBefore(node); range.setEndAfter(node.closest("article")!);
    window.getSelection()?.removeAllRanges(); window.getSelection()?.addRange(range);
  });
  await expect(action).toHaveCount(0);
  await selectText(body, "侦探抵达港口");
  await action.focus(); await page.keyboard.press("Enter");
  await expect(page.locator(".chat-input")).toBeVisible();
  await expect(page.locator(".chat-input")).toBeFocused();
  await expect(page.locator(".text-reference-chip")).toContainText("侦探抵达港口");
});
