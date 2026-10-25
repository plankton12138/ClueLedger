import { test, expect } from "./fixtures/frontendExperience";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve, join } from "node:path";
import type { Page } from "@playwright/test";

async function shot(page: Page, name: string, stage = "H4") {
  const output = resolve("../dev-notes/v0.8/artifacts/frontend-experience/screenshots");
  mkdirSync(output, { recursive: true }); await page.screenshot({ path: `${output}/${stage}-${name}.png` });
}
async function font(page: Page, value: number) {
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const settings = page.getByRole("dialog", { name: "设置", exact: true });
  await settings.getByLabel("调整字体大小").selectOption(String(value));
  await page.keyboard.press("Escape");
}

test("灵感真实列表与历史分页、旧内容预览和删除修订恢复", async ({ page, request, book }) => {
  test.setTimeout(60_000);
  const base = `/api/file-workspaces/${book.id}/ideas`;
  for (let n = 0; n < 51; n++) {
    const created = await request.post(base, { data: { title: `分页灵感${n}`, body: `正文${n}`, tags: [], request_key: `page-${book.id}-${n}` } });
    expect(created.ok()).toBeTruthy();
  }
  let view = await (await request.post(base, { data: { title: "历史核对", body: "最初内容", tags: [], request_key: `history-${book.id}` } })).json();
  for (let n = 0; n < 21; n++) view = await (await request.patch(`${base}/${view.idea_id}`, { data: { expected_digest: view.digest, expected_revision: view.revision_id, title: "历史核对", body: `修订${n}`, request_key: `edit-${book.id}-${n}` } })).json();
  await page.goto(`/#/workspaces/${book.id}/ideas`);
  const list = page.getByRole("complementary", { name: "灵感列表", exact: true });
  await expect(list.getByRole("button", { name: "加载更多灵感", exact: true })).toBeVisible();
  await list.getByRole("button", { name: "加载更多灵感", exact: true }).click();
  await expect(list.getByRole("button", { name: /^旧识 / })).toBeVisible();
  await list.getByRole("button", { name: /^历史核对 / }).click();
  await expect(page.getByLabel("灵感正文", { exact: true })).toHaveValue("修订20");
  const history = page.locator(".idea-history"); await history.locator("summary").click();
  await expect(history.locator("li")).toHaveCount(20);
  await history.getByRole("button", { name: "更多历史", exact: true }).click();
  await expect(history.locator("li")).toHaveCount(22);
  await history.getByRole("button", { name: "查看内容", exact: true }).last().click();
  await expect(page.getByRole("region", { name: "历史内容" })).toContainText("最初内容");
  await history.getByRole("button", { name: "恢复内容", exact: true }).click();
  await expect(page.getByLabel("灵感正文", { exact: true })).toHaveValue("最初内容");
  await page.getByRole("button", { name: "条目操作", exact: true }).click();
  await page.getByRole("dialog", { name: "条目操作" }).getByRole("button", { name: "删除", exact: true }).click();
  await expect(page.getByRole("button", { name: "恢复", exact: true })).toBeVisible();
  if (!await history.evaluate(el => (el as HTMLDetailsElement).open)) await history.locator("summary").click();
  await history.getByRole("button", { name: "查看内容", exact: true }).first().click();
  await expect(page.getByRole("region", { name: "历史内容" })).toContainText("删除修订");
  await expect(history.getByRole("button", { name: "恢复为删除状态", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "恢复", exact: true }).click();
  await expect(page.getByLabel("灵感正文", { exact: true })).toHaveValue("最初内容");
  await expect(page.getByRole("button", { name: "条目操作", exact: true })).toBeVisible();
  await history.locator("li").filter({ hasText: /^删除/ }).getByRole("button", { name: "查看内容", exact: true }).first().click();
  await history.getByRole("button", { name: "恢复为删除状态", exact: true }).click();
  await expect(page.getByRole("button", { name: "恢复", exact: true })).toBeVisible();
  await shot(page, "ideas-history-1440");
});

test("灵感窄屏草稿与主操作矩阵，引用保留聊天草稿且不发送", async ({ page, book }) => {
  let writes = 0;
  page.on("request", req => { if (req.method() === "POST" && /\/turns|\/material-tasks/.test(req.url())) writes++; });
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByPlaceholder("说要求，回车发送，Shift+Enter 换行").fill("保留聊天输入");
  await page.getByRole("link", { name: "灵感", exact: true }).click();
  await page.getByRole("button", { name: /^旧识 / }).click();
  await expect(page.getByLabel("灵感正文", { exact: true })).toHaveValue("侦探认出了雨衣。");
  await page.getByLabel("灵感正文", { exact: true }).fill("新内容保留");
  await expect(page.getByRole("button", { name: "带到对话", exact: true })).toBeDisabled();
  for (const width of [1440, 1024, 760, 640]) for (const scale of [100, 150]) {
    await page.setViewportSize({ width, height: width === 1024 ? 768 : width === 640 ? 800 : 900 }); await font(page, scale);
    await expect(page.getByLabel("灵感正文", { exact: true })).toHaveValue("新内容保留");
    await expect(page.getByRole("button", { name: "保存", exact: true })).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (await page.getByRole("button", { name: "返回列表", exact: true }).isVisible()) {
      await page.getByRole("button", { name: "返回列表", exact: true }).click();
      await page.getByLabel("搜索灵感", { exact: true }).fill("旧识");
      await page.getByRole("button", { name: /^旧识 / }).click();
      await expect(page.getByLabel("灵感正文", { exact: true })).toHaveValue("新内容保留");
    }
    await shot(page, `ideas-${width}-${scale}`);
  }
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("button", { name: "带到对话", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "带到对话", exact: true }).click();
  const chat = page.getByRole("button", { name: /^对话/, exact: false }); if (await chat.isVisible()) await chat.click();
  await expect(page.getByPlaceholder("说要求，回车发送，Shift+Enter 换行")).toHaveValue("保留聊天输入");
  await expect(page.locator(".writing-chat")).toContainText("旧识");
  expect(writes).toBe(0);
});

test("资料读取、顺序与范围选择不调用模型，后台筛选不重置任务范围", async ({ page, request, book }) => {
  writeFileSync(join(book.path, "正文", "0002 港口.md"), "# 港口\n\n另一章。", "utf8");
  const actual = await (await request.get(`/api/file-workspaces/${book.id}/story-materials/story_index`)).json();
  let starts = 0, submitted: any;
  await page.route("**/api/model-connections", async route => {
    const config = await (await route.fetch()).json(); config.selection = { ...config.selection, available: true };
    await route.fulfill({ json: config });
  });
  await page.route(`**/api/file-workspaces/${book.id}/material-tasks`, async route => {
    if (route.request().method() !== "POST") return route.continue();
    starts++; submitted = route.request().postDataJSON();
    await route.fulfill({ status: 409, json: { error: { code: "TEST_SCOPE_CAPTURED", where: "整理范围核对", why: "已截获测试范围", fix: "关闭提示" } } });
  });
  await page.goto(`/#/workspaces/${book.id}/materials/story`);
  await expect(page.getByText("依据已改动", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "重新读取资料", exact: true })).toHaveCount(1);
  await page.getByRole("button", { name: "重新读取资料", exact: true }).click();
  await page.getByRole("button", { name: "按故事时间", exact: true }).click();
  await page.getByRole("button", { name: "按章节", exact: true }).click();
  await page.getByRole("button", { name: "整理所选章节", exact: true }).click();
  const picker = page.getByRole("dialog", { name: "整理章节索引", exact: true });
  await picker.getByRole("checkbox", { name: /第 1 章/ }).uncheck();
  await picker.getByRole("checkbox", { name: /第 2 章/ }).check();
  expect(starts).toBe(0);
  await page.evaluate(id => { location.hash = `#/workspaces/${id}/materials/story?chapter=1`; }, book.id);
  await expect(picker.getByRole("checkbox", { name: /第 2 章/ })).toBeChecked();
  await picker.getByRole("button", { name: "整理所选章节", exact: true }).click();
  await expect.poll(() => starts).toBe(1);
  expect(submitted.scope.chapter_paths).toEqual(["正文/0002 港口.md"]);
  expect(submitted.scope.as_of_chapter).toBe(2);
  expect(submitted.scope.observed_digests).toEqual({ "正文/0002 港口.md": actual.chapters.find((item: any) => item.chapter === 2).digest });
  await expect(page.getByText(/TEST_SCOPE_CAPTURED/)).toBeVisible();
});

test("资料窗口与字号矩阵、无模型范围可查看、合法空结果", async ({ page, book }) => {
  await page.route("**/api/model-connections", async route => {
    const config = await (await route.fetch()).json(); config.selection = { ...config.selection, available: false };
    await route.fulfill({ json: config });
  });
  let starts = 0; page.on("request", req => { if (req.method() === "POST" && /material-tasks/.test(req.url())) starts++; });
  await page.goto(`/#/workspaces/${book.id}/materials/story`);
  await expect(page.getByText("依据已改动", { exact: true }).first()).toBeVisible();
  for (const width of [1440, 1024, 760, 640]) for (const scale of [100, 150]) {
    await page.setViewportSize({ width, height: width === 1024 ? 768 : width === 640 ? 800 : 900 }); await font(page, scale);
    await expect(page.getByRole("button", { name: "整理所选章节", exact: true })).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await shot(page, `materials-${width}-${scale}`);
  }
  await page.getByRole("button", { name: "整理所选章节", exact: true }).click();
  const picker = page.getByRole("dialog", { name: "整理章节索引", exact: true });
  await expect(picker.getByRole("button", { name: "整理所选章节", exact: true })).toBeDisabled();
  await expect(picker).toContainText("连接模型后可启动整理"); await shot(page, "range-640-150");
  await page.keyboard.press("Escape");
  await page.getByRole("navigation", { name: "资料分类" }).getByRole("link", { name: "名词表", exact: true }).click();
  await expect(page.getByText("这个时点下没有资料。", { exact: true })).toBeVisible();
  expect(starts).toBe(0);
});

test("资料结果状态、窄屏依据返回位置与读取失败", async ({ page, request, book }) => {
  const original = await (await request.get(`/api/file-workspaces/${book.id}/story-materials/glossary`)).json();
  const outcomes = ["found", "empty", "insufficient", "unrecorded"];
  const data = { ...original, entries: Array.from({ length: 24 }, (_, n) => ({
    id: `term-${n}`, title: `名词${n}`, subtitle: "隔离资料状态样例", chapter: 1, entity_path: null,
    domain: "glossary", coverage: outcomes[n % 4], freshness: n === 0 ? "stale" : "valid", task_id: "", updated_at: "",
    evidence: n === 0 ? [{ path: "正文/0001 雨夜.md", digest: "old", chapter: 1, start: 0, end: 6, quote: "旧引文保留", freshness: "stale" }] : [], notes: [],
    data: { term: `名词${n}`, kind: "place", label: "地点", aliases: [], first_chapter: 1, source: "text" },
  })), total: 24 };
  let fail = false, writes = 0;
  await page.route(`**/api/file-workspaces/${book.id}/story-materials/glossary*`, route => fail
    ? route.fulfill({ status: 503, json: { error: { code: "MATERIAL_READ_UNAVAILABLE", where: "资料读取", why: "测试读取不可用", fix: "保留当前结果并重试" } } })
    : route.fulfill({ json: data }));
  page.on("request", req => { if (req.method() === "POST" && /material-tasks/.test(req.url())) writes++; });
  await page.setViewportSize({ width: 640, height: 800 });
  await page.goto(`/#/workspaces/${book.id}/materials/glossary`);
  const list = page.locator(".materials-list");
  for (const label of ["有结果", "无发现", "信息不足", "未整理", "来源已修改"]) await expect(list.getByText(label, { exact: true }).first()).toBeVisible();
  await list.getByRole("button", { name: /^名词0 / }).click();
  const evidence = page.locator(".materials-evidence");
  await expect(evidence).toContainText("旧引文保留"); await expect(evidence).toContainText("不能按旧位置高亮");
  await shot(page, "material-evidence-640");
  await page.getByRole("button", { name: "返回资料列表", exact: true }).click();
  await list.getByRole("button", { name: /^名词2 / }).click();
  await expect(evidence).toContainText("整理时信息不足");
  await page.getByRole("button", { name: "返回资料列表", exact: true }).click();
  await list.evaluate(el => { el.scrollTop = el.scrollHeight; });
  const offset = await list.evaluate(el => el.scrollTop);
  await list.getByRole("button", { name: /^名词23 / }).click();
  await page.getByRole("button", { name: "返回资料列表", exact: true }).click();
  await expect.poll(() => list.evaluate(el => el.scrollTop)).toBe(offset);
  fail = true; await page.getByRole("button", { name: "重新读取资料", exact: true }).click();
  await expect(page.getByText(/\[MATERIAL_READ_UNAVAILABLE\]/)).toBeVisible(); await expect(list).toContainText("名词23");
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
    await expect(page.getByRole("button", { name: "保存新版本", exact: true })).toBeInViewport();
    await expect(page.getByLabel("条目正文", { exact: true }).last()).toHaveValue(book.body);
    await shot(page, `library-editor-${width}-${scale}`, "H6");
    await page.getByRole("button", { name: "返回预览", exact: true }).click();
  }
});
