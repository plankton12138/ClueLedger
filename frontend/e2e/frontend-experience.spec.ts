import { test, expect } from "./fixtures/frontendExperience";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";

async function screenshot(page: Page, name: string) {
  const output = resolve(process.env.HASTINGS_E2E_SCREENSHOT_DIR ?? "../dev-notes/v0.8/artifacts/frontend-experience/screenshots");
  mkdirSync(output, { recursive: true });
  await page.screenshot({ path: `${output}/H2-${name}.png` });
}

async function ideaShot(page: Page, name: string) {
  const output = resolve(process.env.HASTINGS_E2E_SCREENSHOT_DIR ?? "../dev-notes/v0.8/artifacts/ideas-experience/screenshots");
  mkdirSync(output, { recursive: true });
  await page.screenshot({ path: `${output}/${name}.png` });
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
  await page.goto(`/#/workspaces/${book.id}/materials/legacy-story`);
  await page.screenshot({ path: `${output}/materials-1440.png` });
  await page.getByRole("link", { name: "模块库", exact: true }).click();
  const dialog = page.getByRole("region", { name: "模块库", exact: true });
  await dialog.getByRole("button", { name: new RegExp(`长记录-${book.id}`) }).click();
  await expect(dialog.getByLabel("模块正文").first()).toBeVisible();
  console.log(`baseline module codepoints: ${Array.from(await dialog.getByLabel("模块正文").last().inputValue()).length}/${Array.from(book.body).length}`);
  await page.screenshot({ path: `${output}/module-1440.png` });
  await dialog.getByRole("button", { name: "返回写作", exact: true }).click();
  await page.getByRole("button", { name: "模型设置", exact: true }).click();
  await page.screenshot({ path: `${output}/settings-1440.png` });
  await page.getByRole("dialog", { name: "模型设置", exact: true }).getByRole("button", { name: "关闭", exact: true }).click();
  await page.getByRole("button", { name: "活动记录", exact: true }).click();
  await page.screenshot({ path: `${output}/activity-1440.png` });
});

test("B04/B06：长原文完整，关闭重开和版本切换保留草稿", async ({ page, book }) => {
  const dialog = await openModule(page, book);
  const body = dialog.getByLabel("模块正文", { exact: true }).last();
  await expect(body).toHaveValue(book.body);
  await body.fill(`${book.body}未发布结尾`);
  await dialog.getByRole("button", { name: "返回写作", exact: true }).click();
  await page.getByRole("link", { name: "模块库", exact: true }).click();
  await dialog.getByRole("button", { name: new RegExp(`长记录-${book.id}`) }).click();
  await editModule(dialog);
  await expect(body).toHaveValue(`${book.body}未发布结尾`);
  await screenshot(page, "module-draft-1440");
  const latest = await dialog.getByLabel("模块版本", { exact: true }).inputValue();
  await dialog.locator(".library-detail-toolbar > .library-more > summary").click();
  await dialog.getByLabel("模块版本", { exact: true }).selectOption(book.revisionId);
  await expect(dialog.locator(".module-preview")).toContainText(book.body);
  await dialog.locator(".library-detail-toolbar > .library-more > summary").click();
  await dialog.getByLabel("模块版本", { exact: true }).selectOption(latest);
  await editModule(dialog);
  await expect(body).toHaveValue(`${book.body}未发布结尾`);
  await dialog.getByRole("button", { name: "返回写作", exact: true }).click();
  await page.getByLabel("选择作品目录").selectOption(book.otherId);
  await page.getByRole("link", { name: "模块库", exact: true }).click();
  await dialog.getByRole("button", { name: new RegExp(`长记录-${book.id}`) }).click();
  await editModule(dialog);
  await expect(body).toHaveValue(`${book.body}未发布结尾`);
});

test("B06：完整发布往返保持长原文、多个条目和字段", async ({ page, book, request }) => {
  const dialog = await openModule(page, book);
  const body = dialog.getByLabel("模块正文").last();
  await expect(body).toHaveValue(book.body);
  await body.fill(`${book.body}尾声`);
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(dialog.getByText("已保存到模块库；其他作品保持原版本。", { exact: true })).toBeVisible();
  await expect(dialog.locator(".module-preview")).toContainText(`${book.body}尾声`);
  const revisions = await request.get(`/api/module-library/modules/${book.moduleId}/revisions`).then(r => r.json());
  expect(revisions.total).toBe(3);
  const savedRevision = await dialog.getByLabel("模块版本", { exact: true }).inputValue();
  const detail = await request.get(`/api/module-library/modules/${book.moduleId}/revisions/${savedRevision}`).then(r => r.json());
  expect(detail.entries).toHaveLength(2);
  expect(detail.entries.find((e: { entry_id: string }) => e.entry_id === "rain").aliases).toEqual([]);
});

test("B06：第二段失败时不显示编辑器，不产生截断版本", async ({ page, book, request }) => {
  await page.route(`**/modules/${book.moduleId}/revisions/*/records/rain?start=*`, async route => {
    if (new URL(route.request().url()).searchParams.get("start") !== "0") await route.abort("failed");
    else await route.continue();
  });
  const dialog = await openModule(page, book, false);
  await expect(dialog.getByText(/MODULE_REQUEST_FAILED/)).toBeVisible();
  await expect(dialog.getByLabel("模块正文")).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "保存", exact: true })).toHaveCount(0);
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
  await dialog.getByRole("button",{name:"返回列表",exact:true}).click();
  await dialog.getByRole("button", { name: new RegExp(`海上城市-${book.id}`) }).click();
  await expect(dialog.locator(".module-preview").getByText("终年起雾。", { exact: true })).toBeVisible();
  const lateResponse = page.waitForResponse(response => response.url().includes(`/modules/${book.moduleId}/`) && response.url().endsWith("records/rain?start=0"));
  release(); await lateResponse;
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await expect(dialog.getByRole("heading", { name: `海上城市-${book.id}` })).toBeVisible();
  await expect(dialog.locator(".module-preview").getByText("终年起雾。", { exact: true })).toBeVisible();
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
  const body = dialog.getByLabel("模块正文").last();
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
  await dialog.getByLabel("模块正文").last().fill("响应丢失后的同一次提交");
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(dialog.getByText(/MODULE_REQUEST_FAILED/)).toBeVisible();
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(dialog.getByText("已保存到模块库；其他作品保持原版本。", { exact: true })).toBeVisible();
  expect(requests).toHaveLength(2); expect(requests[0]).toEqual(requests[1]);
  const revisions = await request.get(`/api/module-library/modules/${book.moduleId}/revisions`).then(r => r.json());
  expect(revisions.total).toBe(3);
});

test("作品中的内容可显式另存到库，原稿保持原版本", async ({ page, book, request }) => {
  const revision=await request.get(`/api/module-library/modules/${book.moduleId}/revisions/${book.revisionId}`).then(r=>r.json());
  const ref={library_id:revision.library_id,module_id:book.moduleId,revision_id:book.revisionId};
  await request.put(`/api/file-workspaces/${book.id}/assembly`,{data:{bindings:[{module:ref}],expected_digest:null,request_key:crypto.randomUUID()}});
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button",{name:`长记录-${book.id}`,exact:true}).click();
  const paper=page.getByRole("article",{name:"模块内容",exact:true});
  await expect(paper).toContainText(book.body);
  const before=await request.get(`/api/module-library/modules?q=${book.id}`).then(r=>r.json());
  await page.getByRole("button",{name:`管理长记录-${book.id}`,exact:true}).click();
  await page.getByRole("dialog").getByRole("button",{name:"另存到模块库",exact:true}).click();
  await expect(paper).toContainText("已另存一份到模块库。");
  const after=await request.get(`/api/module-library/modules?q=${book.id}`).then(r=>r.json());
  expect(after.total).toBe(before.total+1);
  const assembly=await request.get(`/api/file-workspaces/${book.id}/assembly`).then(r=>r.json());
  expect(assembly.bindings[0].module).toEqual(ref);
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
  await page.getByRole("button", { name: "收起修改", exact: true }).click();
  await page.getByLabel("记录灵感", { exact: true }).fill("临时草稿");
  await list.getByRole("button", { name: /^侦探认出了雨衣/ }).click();
  await expect(page.getByLabel("灵感正文", { exact: true })).toHaveValue("未保存的旧识");
  await expect(page.getByLabel("记录灵感", { exact: true })).toHaveValue("临时草稿");
  await page.getByRole("button", { name: "完成", exact: true }).click();
  await expect(page.getByText("已保存为新的灵感修订。", { exact: true })).toBeVisible();
  await screenshot(page, "idea-selected-1440");
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
  await page.goto(`/#/workspaces/${book.id}/materials/legacy-story`);
  await page.getByRole("button", { name: "整理所选章节", exact: true }).click();
  const range = page.getByRole("dialog", { name: "整理章节索引", exact: true });
  await range.getByText(/雨夜/).hover();
  await screenshot(page, "chapter-range-hover-1440");
});

test("对话选段记成灵感，来源定位与原文一致", async ({ page, request, book }) => {
  const stamp = "2026-10-25T10:00:00+08:00";
  await page.route(`**/api/file-workspaces/${book.id}/conversations`, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    await route.fulfill({
      json: {
        items: [{ id: "c1", title: "雨衣讨论", created_at: stamp, updated_at: stamp }],
        recent_id: "c1",
        active_turn: null,
        revision: "r1",
      },
    });
  });
  await page.route(`**/api/file-workspaces/${book.id}/conversations/c1/messages*`, async (route) => {
    await route.fulfill({
      json: {
        events: [
          { seq: 1, at: stamp, type: "turn_started", turn_id: "t1", text: "说说雨衣" },
          {
            seq: 2,
            at: stamp,
            type: "assistant_message",
            turn_id: "t1",
            text: "雨衣上的水痕说明他刚从码头回来。",
          },
        ],
        last_seq: 2,
        active_turn: null,
        revision: "r1",
      },
    });
  });

  await page.goto("/");
  await page.getByLabel("选择作品目录").selectOption(book.id);
  await page.getByLabel("选择对话").selectOption("c1");
  const reply = page.locator(".chat-reply").first();
  await expect(reply).toContainText("雨衣上的水痕");

  const before = await (await request.get(`/api/file-workspaces/${book.id}/ideas?order=created`)).json();

  // 三击选中整段：浮动入口只在稳定消息的选段上出现。
  await reply.locator("p").first().click({ clickCount: 3 });
  await page.getByRole("button", { name: "记成灵感", exact: true }).click();
  await expect(page.getByText(/已记成灵感/)).toBeVisible();
  await ideaShot(page, "selection-capture-1440");

  const after = await (await request.get(`/api/file-workspaces/${book.id}/ideas?order=created`)).json();
  expect(after.total).toBe(before.total + 1);
  const known = new Set(before.items.map((item: { idea_id: string }) => item.idea_id));
  const newest = after.items.find((item: { idea_id: string }) => !known.has(item.idea_id));
  const created = await (
    await request.get(`/api/file-workspaces/${book.id}/ideas/${newest.idea_id}`)
  ).json();
  // 存的是选段原文，不是重写过的句子。
  expect("雨衣上的水痕说明他刚从码头回来。").toContain(created.body);
  expect(created.body.trim().length).toBeGreaterThan(0);
  expect(created.origin).toMatchObject({
    role: "assistant",
    conversation_id: "c1",
    turn_id: "t1",
    message_seq: 1,
  });
  expect(created.origin.quote).toBe(created.body);
});

test("引用显示原文预览，作者明确更新到最新版本", async ({ page, request, book }) => {
  await page.goto("/");
  await page.getByLabel("选择作品目录").selectOption(book.id);
  await page.getByRole("button", { name: "灵感", exact: true }).click();
  const list = page.getByRole("complementary", { name: "灵感列表", exact: true });
  await list.getByRole("button", { name: /^侦探认出了雨衣/ }).click();
  await page.getByRole("button", { name: "加入对话", exact: true }).click();

  const chip = page.getByRole("button", { name: /^查看引用的灵感：侦探认出了雨衣/ });
  await expect(chip).toBeVisible();
  // 引用显示原文预览，不把修订号当日常标签。
  await expect(page.getByText(/irev_[0-9a-f]/)).toHaveCount(0);

  // 作者在别处把这条灵感改成新版本；引用不会自己换版本。
  const listing = await (await request.get(`/api/file-workspaces/${book.id}/ideas`)).json();
  const idea = await (
    await request.get(`/api/file-workspaces/${book.id}/ideas/${listing.items[0].idea_id}`)
  ).json();
  const updated = await request.patch(`/api/file-workspaces/${book.id}/ideas/${idea.idea_id}`, {
    data: {
      expected_digest: idea.digest,
      expected_revision: idea.revision_id,
      body: "侦探认出了雨衣，也认出了那把伞。",
      request_key: `ref-update-${book.id}`,
    },
  });
  expect(updated.ok()).toBeTruthy();

  await chip.click();
  const dialog = page.getByRole("dialog", { name: "引用的灵感", exact: true });
  await expect(dialog).toContainText("侦探认出了雨衣。");
  await expect(dialog.getByText(/已有更新的版本/)).toBeVisible();
  await ideaShot(page, "reference-update-1440");
  await dialog.getByRole("button", { name: "更新到最新版本", exact: true }).click();
  await expect(
    page.getByRole("button", { name: /^查看引用的灵感：侦探认出了雨衣，也认出了那把伞/ }),
  ).toBeVisible();
});

test("展开讨论在未连接模型时说明原因，选择与草稿保留", async ({ page, book }) => {
  // 把模型配置桩成「没有可用连接」：这条用例只验证未配置时的界面状态。
  await page.route("**/api/model-connections", async (route) => {
    await route.fulfill({
      json: {
        revision: "r1",
        connections: [],
        selection: {
          connection_id: null,
          model: "",
          source: "none",
          available: false,
          reason: "还没有可用的模型连接",
        },
        providers: [],
        storage: "system",
      },
    });
  });
  let posts = 0;
  page.on("request", req => {
    if (req.method() === "POST" && /\/conversations$/.test(req.url())) posts++;
  });

  await page.goto("/");
  await page.getByLabel("选择作品目录").selectOption(book.id);
  await page.getByRole("button", { name: "灵感", exact: true }).click();
  const list = page.getByRole("complementary", { name: "灵感列表", exact: true });
  await list.getByRole("button", { name: /^侦探认出了雨衣/ }).click();
  await page.getByLabel("灵感正文", { exact: true }).fill("改了一半的正文");

  await page.locator(".idea-entry-more > summary").click();
  const expand = page.getByRole("button", { name: "展开讨论", exact: true });
  await expect(expand).toBeDisabled();
  await expect(expand).toHaveAttribute("title", "先连接模型再展开");
  expect(posts).toBe(0);

  // 收起再打开：未保存的修改仍在，不会因为按钮不可用被丢掉。
  await page.getByRole("button", { name: "收起灵感", exact: true }).click();
  await page.getByRole("button", { name: "灵感", exact: true }).click();
  await page
    .getByRole("complementary", { name: "灵感列表", exact: true })
    .getByRole("button", { name: /^侦探认出了雨衣/ })
    .click();
  await expect(page.getByLabel("灵感正文", { exact: true })).toHaveValue("改了一半的正文");
  expect(posts).toBe(0);
});

test("展开灵感：一次点击只读讨论，再次点击打开同一次讨论", async ({ page, book }) => {
  const stamp = "2026-10-25T10:00:00+08:00";
  const events = [
    { seq: 1, at: stamp, type: "turn_started", turn_id: "t-expand", text: "展开这条灵感：侦探认出了雨衣。" },
    {
      seq: 2,
      at: stamp,
      type: "assistant_message",
      turn_id: "t-expand",
      text: "三个方向：追查雨衣来源、假装没认出、让对方先开口。",
    },
    { seq: 3, at: stamp, type: "run_finished", turn_id: "t-expand", status: "completed", recording: "no_change" },
  ];
  const accepted = {
    conversation_id: "c-expand",
    turn_id: "t-expand",
    started_at: stamp,
    title: "展开这条灵感",
    request_key: "k-expand",
  };
  const posts: Record<string, unknown>[] = [];

  await page.route(`**/api/file-workspaces/${book.id}/conversations`, async (route) => {
    if (route.request().method() === "POST") {
      posts.push(route.request().postDataJSON() as Record<string, unknown>);
      await route.fulfill({ status: 202, json: accepted });
      return;
    }
    await route.fulfill({
      json: {
        items: [{ id: "c-expand", title: "展开这条灵感", created_at: stamp, updated_at: stamp }],
        recent_id: "c-expand",
        active_turn: null,
        revision: "r1",
      },
    });
  });
  await page.route(`**/api/file-workspaces/${book.id}/conversations/c-expand/messages*`, async (route) => {
    await route.fulfill({ json: { events, last_seq: 3, active_turn: null, revision: "r1" } });
  });
  await page.route(`**/api/file-workspaces/${book.id}/ideas/*/activity*`, async (route) => {
    await route.fulfill({
      json: {
        items: [
          {
            id: "c-expand:1:expanded",
            kind: "expanded",
            idea_id: "idea_1",
            revision_id: "irev_1",
            conversation_id: "c-expand",
            conversation_title: "展开这条灵感",
            conversation_available: true,
            turn_id: "t-expand",
            at: stamp,
          },
        ],
        total: 1,
        complete: true,
        next_cursor: null,
      },
    });
  });
  await page.route(`**/api/file-workspaces/${book.id}/events?**`, async (route) => {
    const body = events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
    await route.fulfill({ status: 200, headers: { "Content-Type": "text/event-stream" }, body });
  });

  await page.goto("/");
  await page.getByLabel("选择作品目录").selectOption(book.id);
  await page.getByRole("button", { name: "灵感", exact: true }).click();
  const list = page.getByRole("complementary", { name: "灵感列表", exact: true });
  await list.getByRole("button", { name: /^侦探认出了雨衣/ }).click();
  await page.locator(".idea-entry-more > summary").click();
  await page.getByRole("button", { name: "展开讨论", exact: true }).click();

  // 面板收起，写作页显示这次只读讨论的回答。
  await expect(page.getByRole("complementary", { name: "灵感", exact: true })).toHaveCount(0);
  await expect(page.locator(".chat-reply")).toContainText("三个方向");
  expect(posts).toHaveLength(1);
  const request = posts[0] as {
    purpose: string;
    context: { idea_references: { idea_id: string }[]; selected_path: null };
  };
  expect(request.purpose).toBe("expand_idea");
  expect(request.context.idea_references).toHaveLength(1);
  expect(request.context.selected_path).toBeNull();

  // 使用记录里能看到这次展开，并能跳回那次讨论。
  await page.getByRole("button", { name: "灵感", exact: true }).click();
  const list2 = page.getByRole("complementary", { name: "灵感列表", exact: true });
  await list2.getByRole("button", { name: /^侦探认出了雨衣/ }).click();
  // 有记录时默认展开，不需要再点一次 summary（再点会收起）。
  const activity = page.locator(".idea-activity");
  await page.locator(".idea-entry-more > summary").click();
  await expect(activity).toContainText("展开讨论");
  await activity.getByRole("button", { name: "展开这条灵感", exact: true }).click();
  await expect(page.locator(".chat-reply")).toContainText("三个方向");

  // 再次点击：打开同一次讨论，不重复发送。
  await page.getByRole("button", { name: "灵感", exact: true }).click();
  const list3 = page.getByRole("complementary", { name: "灵感列表", exact: true });
  await list3.getByRole("button", { name: /^侦探认出了雨衣/ }).click();
  await page.locator(".idea-entry-more > summary").click();
  await page.getByRole("button", { name: "展开讨论", exact: true }).click();
  await expect(page.locator(".chat-reply")).toContainText("三个方向");
  expect(posts).toHaveLength(1);
});

test("用于写作：选定章节后按普通写作用途提交，带固定灵感与目标", async ({ page, book }) => {
  const stamp = "2026-10-25T11:00:00+08:00";
  const events = [
    { seq: 1, at: stamp, type: "turn_started", turn_id: "t-write", text: "把这条灵感用到《0001 雨夜》" },
    {
      seq: 2,
      at: stamp,
      type: "assistant_message",
      turn_id: "t-write",
      text: "已登记要改的区间：只在雨衣那段加入旧识的反应。",
    },
    { seq: 3, at: stamp, type: "run_finished", turn_id: "t-write", status: "completed", recording: "committed" },
  ];
  const accepted = {
    conversation_id: "c-write",
    turn_id: "t-write",
    started_at: stamp,
    title: "把这条灵感用到《0001 雨夜》",
    request_key: "k-write",
  };
  const posts: Record<string, unknown>[] = [];

  // 用于写作要真的提交一轮：模型必须可用。
  await page.route("**/api/model-connections", async (route) => {
    const config = await (await route.fetch()).json();
    config.selection = { ...config.selection, available: true };
    await route.fulfill({ json: config });
  });
  await page.route(`**/api/file-workspaces/${book.id}/conversations`, async (route) => {
    if (route.request().method() === "POST") {
      posts.push(route.request().postDataJSON() as Record<string, unknown>);
      await route.fulfill({ status: 202, json: accepted });
      return;
    }
    await route.fulfill({
      json: {
        // 还没有任何会话：用于写作由这次提交新建一个，而不是接着别人的会话发。
        items: [],
        recent_id: null,
        active_turn: null,
        revision: "r1",
      },
    });
  });
  await page.route(`**/api/file-workspaces/${book.id}/conversations/c-write/messages*`, async (route) => {
    await route.fulfill({ json: { events, last_seq: 3, active_turn: null, revision: "r1" } });
  });
  await page.route(`**/api/file-workspaces/${book.id}/events?**`, async (route) => {
    const body = events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
    await route.fulfill({ status: 200, headers: { "Content-Type": "text/event-stream" }, body });
  });

  await page.goto("/");
  await page.getByLabel("选择作品目录").selectOption(book.id);
  await page.getByRole("button", { name: "灵感", exact: true }).click();
  await page
    .getByRole("complementary", { name: "灵感列表", exact: true })
    .getByRole("button", { name: /^侦探认出了雨衣/ })
    .click();
  await page.locator(".idea-entry-more > summary").click();
  await page.getByRole("button", { name: "用于写作", exact: true }).click();

  const dialog = page.getByRole("dialog", { name: "把这条灵感用到哪一章", exact: true });
  await dialog.getByRole("button", { name: /雨夜/ }).click();

  await expect(page.locator(".chat-reply")).toContainText("已登记要改的区间");
  expect(posts).toHaveLength(1);
  const request = posts[0] as {
    purpose?: string;
    text: string;
    context: { selected_path: string; reference_paths: string[]; idea_references: unknown[] };
  };
  // 用于写作仍是普通写作用途：不是展开，也不带索引或检查参数。
  expect(request.purpose).toBeUndefined();
  expect(request.context.selected_path).toBe("正文/0001 雨夜.md");
  expect(request.context.reference_paths).toEqual([]);
  expect(request.context.idea_references).toHaveLength(1);
  expect(request.text).toContain("《");
  // 面板收起，改动落在写作页。
  await expect(page.getByRole("complementary", { name: "灵感", exact: true })).toHaveCount(0);
});
