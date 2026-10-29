import { test, expect } from "./fixtures/frontendExperience";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve, join } from "node:path";
import type { Page } from "@playwright/test";

async function shot(page: Page, name: string, stage = "H4") {
  const output = resolve(process.env.HASTINGS_E2E_SCREENSHOT_DIR ?? "../dev-notes/v0.8/artifacts/frontend-experience/screenshots");
  mkdirSync(output, { recursive: true }); await page.screenshot({ path: `${output}/${stage}-${name}.png` });
}
async function ideaShot(page: Page, name: string) {
  const output = resolve(process.env.HASTINGS_E2E_SCREENSHOT_DIR ?? "../dev-notes/v0.8/artifacts/ideas-experience/screenshots");
  mkdirSync(output, { recursive: true }); await page.screenshot({ path: `${output}/${name}.png` });
}
async function font(page: Page, value: number) {
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const settings = page.getByRole("dialog", { name: "设置", exact: true });
  await settings.getByLabel("调整字体大小").selectOption(String(value));
  await page.keyboard.press("Escape");
}

test("灵感面板：真实列表分页、历史预览与删除恢复", async ({ page, request, book }) => {
  test.setTimeout(90_000);
  const base = `/api/file-workspaces/${book.id}/ideas`;
  for (let n = 0; n < 51; n++) {
    const created = await request.post(base, { data: { body: `正文${n}`, request_key: `page-${book.id}-${n}` } });
    expect(created.ok()).toBeTruthy();
  }
  let view = await (await request.post(base, { data: { body: "最初内容", request_key: `history-${book.id}` } })).json();
  for (let n = 0; n < 21; n++) view = await (await request.patch(`${base}/${view.idea_id}`, { data: { expected_digest: view.digest, expected_revision: view.revision_id, body: `修订${n}`, request_key: `edit-${book.id}-${n}` } })).json();

  // 旧链接仍然有效：打开展开容器并定位到指定记录。
  await page.goto(`/#/workspaces/${book.id}/ideas`);
  await expect(page.getByRole("complementary", { name: "灵感", exact: true })).toBeVisible();
  const list = page.getByRole("complementary", { name: "灵感列表", exact: true });
  await expect(list.getByRole("button", { name: "加载更多灵感", exact: true })).toBeVisible();
  await list.getByRole("button", { name: "加载更多灵感", exact: true }).click();
  await expect(list.getByRole("button", { name: "加载更多灵感", exact: true })).toHaveCount(0);

  await page.getByLabel("搜索灵感", { exact: true }).fill("修订20");
  await list.getByRole("button", { name: /^修订20/ }).click();
  await expect(page.getByLabel("灵感正文", { exact: true })).toHaveValue("修订20");

  const history = page.locator(".idea-history");
  await page.locator(".idea-entry-more > summary").click();
  await history.locator("summary").click();
  await expect(history.locator("li")).toHaveCount(20);
  await history.getByRole("button", { name: "更多历史", exact: true }).click();
  await expect(history.locator("li")).toHaveCount(22);
  await history.getByRole("button", { name: "查看内容", exact: true }).last().click();
  await expect(page.getByRole("region", { name: "历史内容" })).toContainText("最初内容");
  await history.getByRole("button", { name: "恢复内容", exact: true }).click();
  await expect(page.getByLabel("灵感正文", { exact: true })).toHaveValue("最初内容");

  await page.getByRole("button", { name: "删除", exact: true }).click();
  await expect(page.getByRole("button", { name: "恢复", exact: true })).toBeVisible();
  if (!(await history.evaluate(el => (el as HTMLDetailsElement).open))) await history.locator("summary").click();
  await history.getByRole("button", { name: "查看内容", exact: true }).first().click();
  await expect(page.getByRole("region", { name: "历史内容" })).toContainText("删除修订");
  await page.getByRole("button", { name: "恢复", exact: true }).click();
  await expect(page.getByLabel("灵感正文", { exact: true })).toHaveValue("最初内容");
  await expect(page.getByRole("button", { name: "删除", exact: true })).toBeVisible();
  await ideaShot(page, "history-1440-100");
});

test("灵感面板：长文完整读取后再编辑，保存不改写原文与长度", async ({ page, request, book }) => {
  const body = "长文首段：这段用于验证完整读取。\n" + "雨落在旧港的屋顶上，没有人抬头。".repeat(700);
  const created = await (await request.post(`/api/file-workspaces/${book.id}/ideas`, {
    data: { body, request_key: `long-${book.id}` },
  })).json();

  await page.goto(`/#/workspaces/${book.id}/ideas`);
  const list = page.getByRole("complementary", { name: "灵感列表", exact: true });
  await list.getByRole("button", { name: /^长文首段/ }).click();

  const editor = page.getByLabel("灵感正文", { exact: true });
  // 编辑的是完整原文，不是列表里的摘要。
  await expect.poll(async () => editor.evaluate(el => (el as HTMLTextAreaElement).value.length)).toBe(body.length);
  await expect(editor).toHaveValue(body);

  const edited = `${body}\n尾部补充`;
  await editor.fill(edited);
  // 等到保存请求真正返回，再读服务端内容；只看按钮状态会在保存途中就通过。
  const saved_response = page.waitForResponse(
    response => response.url().includes(`/ideas/${created.idea_id}`) && response.request().method() === "PATCH",
  );
  await page.getByRole("button", { name: "完成", exact: true }).click();
  expect((await saved_response).ok()).toBeTruthy();

  const saved = await (await request.get(`/api/file-workspaces/${book.id}/ideas/${created.idea_id}`)).json();
  expect(saved.body).toBe(edited);
  expect(Array.from(saved.body as string).length).toBe(Array.from(edited).length);
});

test("灵感面板：窄屏草稿与主操作矩阵，引用保留聊天草稿且不发送", async ({ page, book }) => {
  let writes = 0;
  page.on("request", req => { if (req.method() === "POST" && /\/turns|\/operations/.test(req.url())) writes++; });
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByPlaceholder("说要求，回车发送，Shift+Enter 换行").fill("保留聊天输入");
  await page.getByRole("button", { name: "灵感", exact: true }).click();
  const list = page.getByRole("complementary", { name: "灵感列表", exact: true });
  await list.getByRole("button", { name: /^侦探认出了雨衣/ }).click();
  await expect(page.getByLabel("灵感正文", { exact: true })).toHaveValue("侦探认出了雨衣。");
  await page.getByLabel("灵感正文", { exact: true }).fill("新内容保留");
  // 有未保存修改也可以引用：先保存再绑定保存结果的那一版。
  await expect(page.getByRole("button", { name: "加入对话", exact: true })).toBeEnabled();
  for (const width of [1440, 1024, 760, 640]) for (const scale of [100, 150]) {
    await page.setViewportSize({ width, height: width === 1024 ? 768 : width === 640 ? 800 : 900 }); await font(page, scale);
    await expect(page.getByLabel("灵感正文", { exact: true })).toHaveValue("新内容保留");
    await expect(page.getByRole("button", { name: "完成", exact: true })).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await ideaShot(page, `editor-${width}-${scale}`);
  }
  await page.getByRole("button", { name: "完成", exact: true }).click();
  await expect(page.getByRole("button", { name: "加入对话", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "加入对话", exact: true }).click();
  await expect(page.getByPlaceholder("说要求，回车发送，Shift+Enter 换行")).toHaveValue("保留聊天输入");
  await expect(page.locator(".writing-chat")).toContainText("新内容保留");
  expect(writes).toBe(0);
});

test("模块预览与编辑的完整窗口及字号矩阵", async ({ page, book }) => {
  await page.goto(`/#/library/modules/${book.moduleId}?workspace=${book.id}`);
  await expect(page.locator(".module-preview")).toContainText("雨夜原文");
  for (const width of [1440, 1024, 760, 640]) for (const scale of [100, 150]) {
    await page.setViewportSize({ width, height: width === 1024 ? 768 : 800 }); await font(page, scale);
    await expect(page.locator(".module-preview")).toBeVisible();
    await expect(page.getByRole("button", { name: "编辑", exact: true })).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await shot(page, `library-preview-${width}-${scale}`, "H6");
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    await expect(page.getByRole("button", { name: "保存", exact: true })).toBeInViewport();
    await expect(page.getByLabel("模块正文", { exact: true }).last()).toHaveValue(book.body);
    await shot(page, `library-editor-${width}-${scale}`, "H6");
    await page.getByRole("button", { name: "取消", exact: true }).click();
  }
});
