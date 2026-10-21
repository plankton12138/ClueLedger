import { test, expect, type Page } from "@playwright/test";
import { mkdtempSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/**
 * 文件工作区 E2E（0.4 V04-4 / V04-11）。
 *
 * 覆盖浏览器主链路：打开目录 → 章节、角色与固定设定 → 只读阅读 → 改动与历史 →
 * 撤销 → 搜索 → 外部改动刷新 → 布局交互（对话栏显隐、栏宽拖拽、稿纸横线滚动）
 * → 对话框新建作品。
 * 夹具自己经真实接口准备作品（新建 + 两次提交），不调用模型；
 * 真实模型链路需单独取证，见 dev-notes/v0.4/notes/v04-4-frontend.md。
 *
 * 新建作品的用例会改变后端「上次打开的作品」，因此排在最后：之前的用例都依赖夹具作品。
 */

const PORT = process.env.HASTINGS_E2E_PORT ?? "8931";
const BASE = `http://127.0.0.1:${PORT}/api/file-workspaces`;

let workspaceRoot = "";
let bindingId = "";

test.beforeAll(async () => {
  const root = mkdtempSync(join(tmpdir(), "hastings-e2e-"));
  workspaceRoot = join(root, "雪山旅馆");
  const open = await fetch(`${BASE}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: workspaceRoot, create: true }),
  });
  bindingId = (await open.json()).id;
  writeFileSync(
    join(workspaceRoot, "正文", "0001 雨夜.md"),
    "# 雨夜\n\n侦探抵达旅馆时，雪已经下了三天。\n",
    "utf-8",
  );
  writeFileSync(
    join(workspaceRoot, "设定集", "角色小传", "林远.md"),
    "# 林远\n\n身份：私家侦探。曾经担任刑警。\n",
    "utf-8",
  );
  await fetch(`${BASE}/${bindingId}/git/record`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: "写入第一章" }),
  });
  writeFileSync(
    join(workspaceRoot, "设定集", "线索.md"),
    "# 线索\n\n- 前台的手套是湿的\n",
    "utf-8",
  );
  await fetch(`${BASE}/${bindingId}/git/record`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: "补充线索" }),
  });
});

async function open(page: Page) {
  await page.goto("/");
  await expect(page.getByLabel("选择作品目录")).toHaveValue(bindingId, { timeout: 10_000 });
}

/** 对话栏根节点：以「选择对话」为内部锚点，不依赖面板的标签结构。 */
function chatPanel(page: Page) {
  return page.locator("aside").filter({ has: page.getByLabel("选择对话") });
}

/** 按住把手横向拖动一段距离。 */
async function dragHandle(page: Page, name: string, deltaX: number) {
  const handle = page.getByRole("separator", { name });
  const box = await handle.boundingBox();
  if (!box) throw new Error(`找不到把手：${name}`);
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + deltaX, y, { steps: 8 });
  await page.mouse.up();
}

test("目录打开后显示章节、角色分组与固定设定", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("button", { name: "雨夜" })).toBeVisible();
  await expect(page.locator("summary").filter({ hasText: "角色小传" })).toBeVisible();
  for (const title of ["林远", "线索", "真相"]) {
    await expect(page.getByRole("button", { name: title, exact: true })).toBeVisible();
  }
});

test("角色小传可阅读并参与搜索", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "林远", exact: true }).click();
  await expect(page.getByText("身份：私家侦探。曾经担任刑警。")).toBeVisible();
  await page.getByLabel("搜索正文与设定").fill("刑警");
  await page.getByRole("button", { name: "查找", exact: true }).click();
  const result = page.getByRole("button").filter({ hasText: "设定集/角色小传/林远.md" });
  await expect(result).toBeVisible();
  await result.click();
  await page.getByRole("button", { name: "返回目录" }).click();
});

test("稿纸只读显示正文", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "雨夜" }).click();
  await expect(page.getByText("侦探抵达旅馆时")).toBeVisible();
  // 稿纸区域没有任何可编辑控件：正文只能由对话或外部编辑器改动。
  await expect(
    page.locator("article textarea, article input, article [contenteditable='true']"),
  ).toHaveCount(0);
});

test("改动与历史列出提交，撤销后内容回退", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "改动与历史" }).click();
  await expect(page.getByText("补充线索")).toBeVisible();
  await expect(page.getByText("写入第一章")).toBeVisible();

  await page.getByRole("button", { name: "撤销" }).first().click();
  await expect(page.getByText(/Revert/)).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "关闭" }).click();
  await page.getByRole("button", { name: "线索", exact: true }).click();
  await expect(page.getByText("这一页还是空的")).toBeVisible();
});

test("搜索定位到正文，外部改动刷新后可见", async ({ page }) => {
  await open(page);
  await page.getByLabel("搜索正文与设定").fill("旅馆");
  await page.getByRole("button", { name: "查找" }).click();
  await expect(page.getByText(/搜索结果/)).toBeVisible();
  await expect(page.getByText(/正文\/0001 雨夜\.md/).first()).toBeVisible();
  await page.getByRole("button", { name: "返回目录" }).click();

  writeFileSync(
    join(workspaceRoot, "正文", "0002 来信.md"),
    "# 来信\n\n早上，信封从门缝里塞进来。\n",
    "utf-8",
  );
  await page.getByRole("button", { name: "刷新" }).click();
  await expect(page.getByRole("button", { name: "来信" })).toBeVisible();
});

test("章名带章序，人物标签在目录与稿纸一致", async ({ page }) => {
  // 身份写在人物文件开头的元信息里；界面与模型目录使用同一份解析结果。
  writeFileSync(
    join(workspaceRoot, "设定集", "角色小传", "许衡.md"),
    "---\nroles: [suspect, culprit]\n---\n# 许衡\n\n身份：旅馆客人。\n",
    "utf-8",
  );
  await open(page);
  await page.getByRole("button", { name: "刷新" }).click();

  // 章序来自文件编号，不按列表位置重排。
  await expect(page.getByRole("button", { name: "第一章 雨夜" })).toBeVisible();
  await expect(page.getByRole("button", { name: "第二章 来信" })).toBeVisible();

  const row = page.getByRole("button").filter({ hasText: "许衡" });
  await expect(row).toContainText("嫌疑人");
  await expect(row).toContainText("凶手");
  // 颜色靠样式类生效：类名被构建删掉时标签仍有文字，但会退回透明背景。
  const badge = row.locator(".role-badge").filter({ hasText: "凶手" });
  await expect
    .poll(() => badge.evaluate((node) => getComputedStyle(node).backgroundColor))
    .not.toBe("rgba(0, 0, 0, 0)");
  await row.click();

  const paper = page.locator("article");
  await expect(paper).toContainText("身份：旅馆客人。");
  await expect(paper).toContainText("嫌疑人");
  await expect(paper).toContainText("凶手");
  // 元信息只用于标签，不当作小说正文显示。
  await expect(paper).not.toContainText("roles:");
});

test("对话栏可隐藏，隐藏状态刷新后保持", async ({ page }) => {
  await open(page);
  const panel = chatPanel(page);
  const section = page.locator("main > section");
  await expect(panel).toBeVisible();
  const narrow = (await section.boundingBox())!.width;

  // 顶栏开关与面板里的「新对话」按钮同名不同义，必须精确匹配。
  await page.getByRole("button", { name: "对话", exact: true }).click();
  await expect(panel).toHaveCount(0);

  // 收起后稿纸区接过对话栏与把手的宽度。
  await expect
    .poll(async () => Math.round((await section.boundingBox())!.width))
    .toBeGreaterThan(Math.round(narrow) + 300);

  await page.reload();
  await expect(page.getByLabel("选择作品目录")).not.toHaveValue("", { timeout: 10_000 });
  await expect(chatPanel(page)).toHaveCount(0);

  await page.getByRole("button", { name: "对话", exact: true }).click();
  await expect(chatPanel(page)).toBeVisible();
});

test("拖拽把手改栏宽，刷新后保持，双击恢复默认", async ({ page }) => {
  await open(page);
  const nav = page.locator("main > nav");
  const before = (await nav.boundingBox())!.width;

  await dragHandle(page, "目录栏宽度", 60);
  await expect
    .poll(async () => Math.round((await nav.boundingBox())!.width))
    .toBe(Math.round(before) + 60);

  // 宽度写进本地偏好，刷新后保持。
  await page.reload();
  await expect(page.getByLabel("选择作品目录")).not.toHaveValue("", { timeout: 10_000 });
  await expect
    .poll(async () => Math.round((await nav.boundingBox())!.width))
    .toBe(Math.round(before) + 60);

  await page.getByRole("separator", { name: "目录栏宽度" }).dblclick();
  await expect
    .poll(async () => Math.round((await nav.boundingBox())!.width))
    .toBe(Math.round(before));
});

test("对话栏宽度可拖拽，且不超过上限", async ({ page }) => {
  await open(page);
  const panel = chatPanel(page);
  const before = (await panel.boundingBox())!.width;

  // 右栏把手方向相反：指针左移变宽。
  await dragHandle(page, "对话栏宽度", -80);
  await expect
    .poll(async () => Math.round((await panel.boundingBox())!.width))
    .toBe(Math.round(before) + 80);

  // 继续向左拖到超过上限，宽度停在 560。
  await dragHandle(page, "对话栏宽度", -400);
  await expect
    .poll(async () => Math.round((await panel.boundingBox())!.width))
    .toBe(560);
});

test("稿纸横线随内容滚动", async ({ page }) => {
  const long = Array.from({ length: 60 }, (_, index) => `第 ${index + 1} 段：雪还在下。`).join(
    "\n\n",
  );
  writeFileSync(join(workspaceRoot, "正文", "0003 长夜.md"), `# 长夜\n\n${long}\n`, "utf-8");

  await open(page);
  await page.getByRole("button", { name: "刷新" }).click();
  await page.getByRole("button", { name: "长夜" }).click();
  await expect(page.getByText("第 1 段")).toBeVisible();

  const lined = page.locator(".paper-lined");
  const sheet = page.locator(".paper-sheet");

  // 横线挂在内容层，不挂在滚动容器上。
  expect(await lined.evaluate((el) => getComputedStyle(el).backgroundImage)).toContain(
    "repeating-linear-gradient",
  );
  expect(await sheet.evaluate((el) => getComputedStyle(el).backgroundImage)).not.toContain(
    "repeating-linear-gradient",
  );

  const before = (await lined.boundingBox())!.y;
  await sheet.evaluate((el) => {
    el.scrollTop = 160;
  });
  const after = (await lined.boundingBox())!.y;
  expect(Math.round(before - after)).toBeGreaterThan(120);
});

test("新建作品用系统窗口选位置，界面不再出现路径输入框", async ({ page }) => {
  await page.route("**/default-location", (route) =>
    route.fulfill({ json: { root: "C:\\Users\\someone\\Documents" } }),
  );
  await open(page);
  await page.getByRole("button", { name: "打开 / 新建" }).click();
  await page.getByRole("tab", { name: "新建作品" }).click();

  await expect(page.getByText("C:\\Users\\someone\\Documents", { exact: true })).toBeVisible();
  await expect(page.getByLabel("作品名")).toHaveValue("未命名作品");
  // 路径只能来自系统对话框：界面上没有可手输的路径框。
  await expect(page.getByLabel("目录路径")).toHaveCount(0);
});

test("选好位置并起名后新建作品，建立版本记录", async ({ page }) => {
  const parent = mkdtempSync(join(tmpdir(), "hastings-e2e-new-"));
  await page.route("**/pick-directory", (route) => route.fulfill({ json: { path: parent } }));
  await open(page);
  await page.getByRole("button", { name: "打开 / 新建" }).click();
  await page.getByRole("tab", { name: "新建作品" }).click();
  await page.getByRole("button", { name: "更改位置…" }).click();
  await expect(page.getByText(parent, { exact: true })).toBeVisible();
  await page.getByLabel("作品名").fill("新作品");
  await page.getByRole("button", { name: "新建并打开" }).click();

  const created = join(parent, "新作品");
  await page.waitForFunction((path) => document.body.innerText.includes(path), created, {
    timeout: 10_000,
  });
  await page.getByRole("button", { name: "改动与历史" }).click();
  await expect(page.getByText("初始化作品目录")).toBeVisible();
  expect(existsSync(join(created, ".git"))).toBe(true);
});
