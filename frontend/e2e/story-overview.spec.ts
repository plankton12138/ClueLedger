import { test, expect } from "@playwright/test";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { resolve, join } from "node:path";
import { execFileSync } from "node:child_process";

const dataDir = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e");
const shots = resolve(process.env.HASTINGS_E2E_SCREENSHOT_DIR ?? "../data/test-tmp/story-overview-screenshots");
async function fixture(request: import("@playwright/test").APIRequestContext, organized = true) {
  mkdirSync(join(dataDir, "books"), { recursive: true });
  const root = mkdtempSync(join(dataDir, "books", "overview-"));
  const book = await request.post("/api/file-workspaces", { data: { path: root, create: true } }).then(r => r.json());
  writeFileSync(join(root, "正文", "0001 雨夜.md"), "# 雨夜\n\n林远说他没见过铜钥匙，侦探尚未核实。", "utf8");
  writeFileSync(join(root, "正文", "0002 船坞.md"), "# 船坞\n\n林远用铜钥匙打开了废弃船坞。", "utf8");
  const created = await request.post("/api/module-library/modules", { data: { kind: "reference", name: "港口设定", entries: [{ entry_id: "content", title: "港口设定", body: "林远是一名船医。𠮷是林远的姐姐。铜钥匙可以打开废弃船坞的侧门。作者计划第九章揭晓来历。" }], request_key: crypto.randomUUID() } }).then(r => r.json());
  const detail = await request.get(`/api/module-library/modules/${created.module.module_id}/revisions/${created.module.head}`).then(r => r.json());
  const ref = { library_id: detail.library_id, module_id: created.module.module_id, revision_id: created.module.head };
  const assembly = await request.put(`/api/file-workspaces/${book.id}/assembly`, { data: { bindings: [{ module: ref, label: "旅馆人物与物件" }], expected_digest: null, request_key: crypto.randomUUID() } }).then(r => r.json());
  if (organized) execFileSync("uv", ["run", "--directory", "..", "python", resolve("e2e/fixtures/story_overview.py"), root, join(dataDir, "state", "workspaces", book.id), join(dataDir, "library")], { windowsHide: true, stdio: "pipe" });
  return { book, assembly };
}

test("零模块默认时间线；纯浏览不发起整理；无模型可读", async ({ page, request }) => {
  mkdirSync(dataDir, { recursive: true });
  const book = await request.post("/api/file-workspaces", { data: { path: mkdtempSync(join(dataDir, "empty-overview-")), create: true } }).then(r => r.json());
  let modelCommands = 0;
  page.on("request", r => { if (r.method() === "POST" && /\/updates|\/conversations|\/material-tasks/.test(r.url())) modelCommands++; });
  await page.goto(`/#/workspaces/${book.id}/overview`);
  await expect(page.getByRole("heading", { name: "从第一章开始" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "总览内容" }).getByRole("button")).toHaveCount(1);
  await page.getByRole("button", { name: "按故事时间", exact: true }).click();
  await expect(page.getByRole("heading", { name: "还没有可排列的故事事件" })).toBeVisible();
  expect(modelCommands).toBe(0);
});

test("未整理模块立即读原文，更新入口直接接到模型设置", async ({ page, request }) => {
  await page.route("**/api/model-connections", route => route.fulfill({ json: { connections: [], revision: "none", selection: { available: false, source: "none", connection_id: null, model: "" } } }));
  const { book } = await fixture(request, false);
  await page.goto(`/#/workspaces/${book.id}/overview`);
  await expect(page.getByRole("button", { name: "阅读正文", exact: true })).toHaveCount(2);
  await page.getByRole("navigation", { name: "总览内容" }).getByRole("button", { name: "旅馆人物与物件", exact: true }).click();
  await expect(page.locator(".overview-content-card")).toContainText("林远是一名船医");
  await expect(page.getByRole("navigation", { name: "模块视图" })).toHaveCount(0);
  await page.getByRole("button", { name: "更新总览", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "模型设置", exact: true })).toBeVisible();
});

test("真实混合投影：事件到人物线索、关系和原文互通，截止章与刷新可恢复", async ({ page, request }) => {
  const { book } = await fixture(request);
  const errors: string[] = []; page.on("pageerror", e => errors.push(e.message));
  await page.goto(`/#/workspaces/${book.id}/overview`);
  await page.getByLabel("查看到第几章").selectOption("1");
  await expect(page.locator(".overview-event-card")).toHaveCount(1);
  await page.locator(".overview-event-card").getByRole("button", { name: "林远", exact: true }).click();
  await expect(page.getByRole("region", { name: "人物关系", exact: true })).toContainText("姐弟");
  await expect(page.locator(".overview-observations li")).toHaveCount(1);
  await page.getByRole("button", { name: "作者设定依据", exact: true }).click();
  await expect(page.getByRole("complementary", { name: "原文依据", exact: true })).toContainText("已核对原文版本");
  await page.reload();
  await expect(page.getByRole("complementary", { name: "对象详情", exact: true })).toContainText("林远");
  await page.getByRole("navigation", { name: "模块视图" }).getByRole("button", { name: "线索", exact: true }).click();
  await page.getByRole("button", { name: /铜钥匙.*带缺口/ }).click();
  await expect(page.locator(".overview-object-detail")).toContainText("作者计划第九章");
  await expect(page.locator(".overview-observations li")).toHaveCount(1);
  await page.getByRole("button", { name: "查看事件", exact: true }).click();
  await expect(page.locator(".overview-event-card")).toHaveCount(1);
  await page.getByRole("button", { name: "原文依据", exact: true }).click();
  await expect(page.getByRole("complementary", { name: "原文依据", exact: true })).toContainText("已核对原文版本");
  expect(errors).toEqual([]);
});

test("卸下后不保留旧图；旧链接和旧资料入口可达", async ({ page, request }) => {
  const { book } = await fixture(request);
  await page.goto(`/#/workspaces/${book.id}/materials/timeline?chapter=1`);
  await expect(page.getByRole("button", { name: "按故事时间", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("navigation", { name: "总览内容" }).getByRole("button", { name: "旅馆人物与物件" }).click();
  await page.locator(".overview-object-card").filter({ hasText: "林远" }).last().click();
  const current = await request.get(`/api/file-workspaces/${book.id}/assembly`).then(r => r.json());
  await request.put(`/api/file-workspaces/${book.id}/assembly`, { data: { bindings: [], expected_digest: current.digest, request_key: crypto.randomUUID() } });
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByRole("region", { name: "人物关系", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "时间线", exact: true }).click();
  await page.locator(".overview-toolbar").getByText("更多", { exact: true }).click();
  await page.getByRole("link", { name: "旧版资料", exact: true }).click();
  await expect(page).toHaveURL(/materials\/characters/);
});

test("各窗口与大字号保持可阅读和可操作", async ({ page, request }) => {
  const { book } = await fixture(request);
  mkdirSync(shots, { recursive: true });
  for (const scale of [100, 150]) for (const width of [1440, 1024, 760, 640, 390]) {
    await page.setViewportSize({ width, height: 960 });

    await page.goto(`/#/workspaces/${book.id}/overview`);
    await page.evaluate(value => { const prefs = JSON.parse(localStorage.getItem("hastings.workspace.layout") ?? "{}"); localStorage.setItem("hastings.workspace.layout", JSON.stringify({ ...prefs, fontScale: value })); }, scale);
    await page.reload();
    await expect(page.locator(".overview-event-card")).toHaveCount(2);
    expect(await page.evaluate(() => document.documentElement.style.getPropertyValue("--font-scale"))).toBe(String(scale / 100));
    expect((await page.locator(".story-overview").boundingBox())!.width).toBeGreaterThan(width - 2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
    await page.screenshot({ path: join(shots, `timeline-${width}-${scale}.png`) });
    await page.getByRole("navigation", { name: "总览内容" }).getByRole("button", { name: "旅馆人物与物件" }).click();
    await page.locator(".overview-object-card").last().click();
    await expect(page.getByRole("region", { name: "人物关系", exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
    await page.screenshot({ path: join(shots, `characters-${width}-${scale}.png`) });
  }
});
