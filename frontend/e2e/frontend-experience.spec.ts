import { test, expect } from "./fixtures/frontendExperience";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";

async function screenshot(page: Page, name: string) {
  const output = resolve("../dev-notes/v0.8/artifacts/frontend-experience/screenshots");
  mkdirSync(output, { recursive: true });
  await page.screenshot({ path: `${output}/H2-${name}.png` });
}

async function editModule(library: ReturnType<Page["getByRole"]>) {
  const resume = library.getByRole("button", { name: "继续编辑", exact: true });
  if (await resume.isVisible()) await resume.click();
  else await library.getByRole("button", { name: "编辑", exact: true }).click();
}
async function openModule(page: Page, book: { id: string }, editing = true) {
  await page.goto("/");
  await page.getByLabel("选择作品目录").selectOption(book.id);
  await page.getByRole("link", { name: "模块库", exact: true }).click();
  const dialog = page.getByRole("region", { name: "模块库", exact: true });
  await dialog.getByLabel("搜索模块").fill(book.id);
  await dialog.getByLabel("搜索模块").press("Enter");
  await dialog.getByRole("button", { name: new RegExp(`长记录-${book.id}`) }).click();
  if (editing) await editModule(dialog);
  return dialog;
}

test("隔离基线：主要页面与窄窗口正文", async ({ page, book }) => {
  test.skip(!process.env.HASTINGS_UX_BASELINE, "只在建立当前基线时执行");
  const output = resolve("../dev-notes/v0.8/artifacts/frontend-experience/baseline");
  mkdirSync(output, { recursive: true });
  await page.goto("/");
  await page.getByLabel("选择作品目录").selectOption(book.id);
  await page.getByRole("button", { name: "第一章 雨夜", exact: true }).click();
  for (const width of [1440, 1024, 640]) {
    await page.setViewportSize({ width, height: 900 });
    await page.screenshot({ path: `${output}/writing-${width}.png` });
    const size = await page.locator(".paper-sheet").evaluate(el => ({ width: el.clientWidth, height: el.clientHeight }));
    console.log(`baseline paper ${width}: ${JSON.stringify(size)}`);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: "灵感", exact: true }).click();
  await page.screenshot({ path: `${output}/ideas-1440.png` });
  await page.getByRole("link", { name: "故事资料", exact: true }).click();
  await page.screenshot({ path: `${output}/materials-1440.png` });
  await page.getByRole("link", { name: "模块库", exact: true }).click();
  const dialog = page.getByRole("region", { name: "模块库", exact: true });
  await dialog.getByRole("button", { name: new RegExp(`长记录-${book.id}`) }).click();
  await expect(dialog.getByLabel("条目正文").first()).toBeVisible();
  console.log(`baseline module codepoints: ${Array.from(await dialog.getByLabel("条目正文").last().inputValue()).length}/${Array.from(book.body).length}`);
  await page.screenshot({ path: `${output}/module-1440.png` });
  await dialog.getByRole("button", { name: "返回", exact: true }).click();
  await page.getByRole("button", { name: "模型设置", exact: true }).click();
  await page.screenshot({ path: `${output}/settings-1440.png` });
  await page.getByRole("dialog", { name: "模型设置", exact: true }).getByRole("button", { name: "关闭", exact: true }).click();
  await page.getByRole("button", { name: "活动记录", exact: true }).click();
  await page.screenshot({ path: `${output}/activity-1440.png` });
});

test("B04/B06：长原文完整，关闭重开和版本切换保留草稿", async ({ page, book }) => {
  const dialog = await openModule(page, book);
  const body = dialog.getByLabel("条目正文", { exact: true }).last();
  await expect(body).toHaveValue(book.body);
  await body.fill(`${book.body}未发布结尾`);
  await dialog.getByRole("button", { name: "返回", exact: true }).click();
  await page.getByRole("link", { name: "模块库", exact: true }).click();
  await dialog.getByRole("button", { name: new RegExp(`长记录-${book.id}`) }).click();
  await editModule(dialog);
  await expect(body).toHaveValue(`${book.body}未发布结尾`);
  await screenshot(page, "module-draft-1440");
  const latest = await dialog.getByLabel("模块版本", { exact: true }).inputValue();
  await dialog.getByLabel("模块版本", { exact: true }).selectOption(book.revisionId);
  await expect(dialog.locator(".module-preview")).toContainText(book.body);
  await dialog.getByLabel("模块版本", { exact: true }).selectOption(latest);
  await editModule(dialog);
  await expect(body).toHaveValue(`${book.body}未发布结尾`);
  await dialog.getByRole("button", { name: "返回", exact: true }).click();
  await page.getByLabel("选择作品目录").selectOption(book.otherId);
  await page.getByRole("link", { name: "模块库", exact: true }).click();
  await dialog.getByRole("button", { name: new RegExp(`长记录-${book.id}`) }).click();
  await editModule(dialog);
  await expect(body).toHaveValue(`${book.body}未发布结尾`);
});

test("B06：完整发布往返保持长原文、多个条目和字段", async ({ page, book, request }) => {
  const dialog = await openModule(page, book);
  const body = dialog.getByLabel("条目正文").last();
  await expect(body).toHaveValue(book.body);
  await body.fill(`${book.body}尾声`);
  await dialog.getByLabel("条目别名").last().fill("雨, 雨夜");
  await dialog.getByRole("button", { name: "保存新版本", exact: true }).click();
  await expect(dialog.getByText("已保存到模块库；其他作品保持原版本。", { exact: true })).toBeVisible();
  await expect(body).toHaveValue(`${book.body}尾声`);
  const revisions = await request.get(`/api/module-library/modules/${book.moduleId}/revisions`).then(r => r.json());
  expect(revisions.total).toBe(3);
  const savedRevision = await dialog.getByLabel("模块版本", { exact: true }).inputValue();
  const detail = await request.get(`/api/module-library/modules/${book.moduleId}/revisions/${savedRevision}`).then(r => r.json());
  expect(detail.entries).toHaveLength(2);
  expect(detail.entries.find((e: { entry_id: string }) => e.entry_id === "rain").aliases).toEqual(["雨", "雨夜"]);
});

test("B06：第二段失败时不显示编辑器，不产生截断版本", async ({ page, book, request }) => {
  await page.route(`**/modules/${book.moduleId}/revisions/*/records/rain?start=*`, async route => {
    if (new URL(route.request().url()).searchParams.get("start") !== "0") await route.abort("failed");
    else await route.continue();
  });
  const dialog = await openModule(page, book, false);
  await expect(dialog.getByText(/MODULE_REQUEST_FAILED/)).toBeVisible();
  await expect(dialog.getByLabel("条目正文")).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "保存新版本", exact: true })).toHaveCount(0);
  const revisions = await request.get(`/api/module-library/modules/${book.moduleId}/revisions`).then(r => r.json());
  expect(revisions.total).toBe(2);
});

test("B05：A慢B快，迟到原文不会切回A", async ({ page, book }) => {
  let release!: () => void;
  const gate = new Promise<void>(done => { release = done; });
  let started!: () => void;
  const pending = new Promise<void>(done => { started = done; });
  await page.route(`**/modules/${book.moduleId}/revisions/*/records/rain?start=0`, async route => {
    started(); await gate; await route.continue();
  });
  const dialog = await openModule(page, book, false);
  await pending;
  await dialog.getByRole("button", { name: new RegExp(`海上城市-${book.id}`) }).click();
  await expect(dialog.getByText("终年起雾。", { exact: true })).toBeVisible();
  const lateResponse = page.waitForResponse(response => response.url().includes(`/modules/${book.moduleId}/`) && response.url().endsWith("records/rain?start=0"));
  release(); await lateResponse;
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await expect(dialog.getByRole("heading", { name: `海上城市-${book.id}` })).toBeVisible();
  await expect(dialog.getByText("终年起雾。", { exact: true })).toBeVisible();
});

test("B03：640px正文可达", async ({ page, book }) => {
  await page.goto("/");
  await page.getByLabel("选择作品目录").selectOption(book.id);
  await page.getByRole("button", { name: "第一章 雨夜", exact: true }).click();
  await page.setViewportSize({ width: 640, height: 800 });
  expect(await page.locator(".paper-sheet").evaluate(el => el.clientWidth)).toBeGreaterThan(500);
});

test("未保存内容刷新前提示，取消后仍可编辑", async ({ page, book }) => {
  const dialog = await openModule(page, book);
  const body = dialog.getByLabel("条目正文").last();
  await expect(body).toHaveValue(book.body);
  await body.fill("会话草稿");
  const shown = page.waitForEvent("dialog");
  // 取消导航不会产生load事件，给导航等待设置独立上限。
  const reload = page.reload({ timeout: 1500 }).catch(() => null);
  const prompt = await shown;
  expect(prompt.type()).toBe("beforeunload"); await prompt.dismiss();
  await expect(body).toHaveValue("会话草稿");
  await reload;
});

test("UI08：发布响应丢失后同键重试只产生一个新版本", async ({ page, book, request }) => {
  const requests: unknown[] = [];
  await page.route(`**/api/module-library/modules/${book.moduleId}/revisions`, async route => {
    if (route.request().method() !== "POST") return route.continue();
    requests.push(route.request().postDataJSON());
    if (requests.length === 1) { await route.fetch(); await route.abort("failed"); }
    else await route.continue();
  });
  const dialog = await openModule(page, book);
  await dialog.getByLabel("条目正文").last().fill("响应丢失后的同一次提交");
  await dialog.getByRole("button", { name: "保存新版本", exact: true }).click();
  await expect(dialog.getByText(/MODULE_REQUEST_FAILED/)).toBeVisible();
  await dialog.getByRole("button", { name: "保存新版本", exact: true }).click();
  await expect(dialog.getByText("已保存到模块库；其他作品保持原版本。", { exact: true })).toBeVisible();
  expect(requests).toHaveLength(2); expect(requests[0]).toEqual(requests[1]);
  const revisions = await request.get(`/api/module-library/modules/${book.moduleId}/revisions`).then(r => r.json());
  expect(revisions.total).toBe(3);
});

test("UI09：真实发布部分成功后，只重试应用已发布版本", async ({ page, book, request }) => {
  let publications = 0;
  await page.route(`**/api/file-workspaces/${book.id}/module-updates`, async route => {
    publications++;
    const response = await route.fetch({ postData: JSON.stringify({ ...route.request().postDataJSON(), expected_digest: "stale-fixture" }) });
    await route.fulfill({ response });
  });
  const dialog = await openModule(page, book);
  await dialog.getByLabel("条目正文").last().fill("发布成功，应用需要重试");
  await dialog.getByRole("checkbox", { name: /同时用于/ }).check();
  await dialog.getByRole("button", { name: "保存新版本", exact: true }).click();
  await expect(dialog.getByText(/本作品绑定没有更新/)).toBeVisible();
  await screenshot(page, "published-pending-application-1440");
  await dialog.getByRole("button", { name: "重试应用已发布版本", exact: true }).click();
  await expect(dialog.getByText("已将已发布版本用于本作品。", { exact: true })).toBeVisible();
  expect(publications).toBe(1);
  const revisions = await request.get(`/api/module-library/modules/${book.moduleId}/revisions`).then(r => r.json());
  expect(revisions.total).toBe(3);
  const assembly = await request.get(`/api/file-workspaces/${book.id}/assembly`).then(r => r.json());
  expect(assembly.bindings[0].module.revision_id).toBe(await dialog.getByLabel("模块版本", { exact: true }).inputValue());
});

test("B12：撤销先展示提交和文件，取消不执行，确认期间只提交一次", async ({ page, book }) => {
  const commit = "abc1234567890";
  const base = `**/api/file-workspaces/${book.id}`;
  await page.route(`${base}/conversation-ui`, route => route.fulfill({ json: { recent_id: "c1", drafts: {}, revision: "r1" } }));
  await page.route(`${base}/conversations`, route => route.fulfill({ json: { items: [{ id: "c1", title: "撤销夹具", created_at: "", updated_at: "" }], recent_id: "c1", active_turn: null, revision: "r1" } }));
  await page.route(`${base}/conversations/c1/messages?*`, route => route.fulfill({ json: { events: [
    { seq: 1, at: "", type: "turn_started", turn_id: "t1", text: "修改雨夜" },
    { seq: 2, at: "", type: "run_finished", turn_id: "t1", status: "completed", recording: "committed", commit, changed_paths: ["正文/0001 雨夜.md"] },
  ], last_seq: 2, active_turn: null, revision: "r1" } }));
  let calls = 0;
  let release!: () => void;
  const gate = new Promise<void>(done => { release = done; });
  await page.route(`${base}/git/revert`, async route => { calls++; expect(route.request().postDataJSON()).toEqual({ commit }); await gate; await route.fulfill({ json: { commit: "reverted" } }); });
  await page.goto("/");
  await page.getByLabel("选择作品目录").selectOption(book.id);
  await page.getByRole("button", { name: "撤销这次修改", exact: true }).click();
  const confirm = page.getByRole("alertdialog", { name: "撤销这次修改", exact: true });
  await expect(confirm).toContainText(commit);
  await expect(confirm).toContainText("正文/0001 雨夜.md");
  await screenshot(page, "revert-confirmation-1440");
  await confirm.getByRole("button", { name: "取消", exact: true }).click();
  expect(calls).toBe(0);
  await page.getByRole("button", { name: "撤销这次修改", exact: true }).click();
  await confirm.getByRole("button", { name: "确认撤销", exact: true }).click();
  await expect(confirm.getByRole("button", { name: "撤销中…", exact: true })).toBeDisabled();
  expect(calls).toBe(1); release();
  await expect(confirm).toBeHidden();
});

test("B11：无模型发送禁用，草稿和连接入口保留", async ({ page, book }) => {
  await page.route("**/api/model-connections", route => route.fulfill({ json: { connections: [], revision: "none", selection: { available: false, source: "none", connection_id: null, model: "" } } }));
  await page.goto("/");
  await page.getByLabel("选择作品目录").selectOption(book.id);
  await page.locator(".chat-input").fill("还没有连接时的草稿");
  await expect(page.getByRole("button", { name: "发送", exact: true })).toBeDisabled();
  await expect(page.locator(".chat-input")).toHaveValue("还没有连接时的草稿");
  await expect(page.getByRole("button", { name: "去设置", exact: true })).toBeVisible();
  await screenshot(page, "no-model-draft-1440");
});

test("灵感：快速记录与条目修改稿分开保留，无模型保存", async ({ page, book }) => {
  await page.goto("/");
  await page.getByLabel("选择作品目录").selectOption(book.id);
  await page.getByRole("button", { name: "灵感", exact: true }).click();
  const list = page.getByRole("complementary", { name: "灵感列表", exact: true });
  await list.getByRole("button", { name: /^侦探认出了雨衣/ }).click();
  await expect(page.getByLabel("灵感正文", { exact: true })).toHaveValue("侦探认出了雨衣。");
  await page.getByLabel("灵感正文", { exact: true }).fill("未保存的旧识");
  await page.getByRole("button", { name: "返回列表", exact: true }).click();
  await page.getByLabel("记录灵感", { exact: true }).fill("临时草稿");
  await list.getByRole("button", { name: /^侦探认出了雨衣/ }).click();
  await expect(page.getByLabel("灵感正文", { exact: true })).toHaveValue("未保存的旧识");
  await expect(page.getByLabel("记录灵感", { exact: true })).toHaveValue("临时草稿");
  await page.getByRole("button", { name: "完成", exact: true }).click();
  await expect(page.getByText("已保存为新的灵感修订。", { exact: true })).toBeVisible();
  await screenshot(page, "idea-selected-1440");
  await page.getByRole("button", { name: "返回列表", exact: true }).click();
  await expect(list.getByRole("button", { name: /^未保存的旧识/ })).toBeVisible();
  await expect(page.getByLabel("记录灵感", { exact: true })).toHaveValue("临时草稿");
});

test("中文输入法确认不发送，章节范围悬停仍可读", async ({ page, book }) => {
  await page.goto("/");
  await page.getByLabel("选择作品目录").selectOption(book.id);
  let sends = 0;
  await page.route(`**/api/file-workspaces/${book.id}/conversations`, async route => {
    if (route.request().method() === "POST") { sends++; await route.abort(); }
    else await route.continue();
  });
  await page.locator(".chat-input").fill("中文候选");
  await page.locator(".chat-input").dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true, keyCode: 229 });
  expect(sends).toBe(0);
  await expect(page.locator(".chat-input")).toHaveValue("中文候选");
  await page.getByRole("link", { name: "故事资料", exact: true }).click();
  await page.getByRole("button", { name: "整理所选章节", exact: true }).click();
  const range = page.getByRole("dialog", { name: "整理章节索引", exact: true });
  await range.getByText(/雨夜/).hover();
  await screenshot(page, "chapter-range-hover-1440");
});
