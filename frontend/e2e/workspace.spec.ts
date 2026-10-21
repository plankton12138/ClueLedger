import { test, expect, type Page } from "@playwright/test";
import { mkdtempSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/**
 * 文件工作区 E2E（0.4 V04-4）。
 *
 * 覆盖浏览器主链路：打开目录 → 章节与三份设定 → 只读阅读 → 改动与历史 →
 * 撤销 → 搜索 → 外部改动刷新 → 对话框新建作品。
 * 夹具自己经真实接口准备作品（新建 + 两次提交），不调用模型；
 * 真实模型链路需单独取证，见 dev-notes/v0.4/notes/v04-4-frontend.md。
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

test("目录打开后显示章节与三份设定", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("button", { name: "雨夜" })).toBeVisible();
  for (const title of ["角色小传", "线索", "真相"]) {
    await expect(page.getByRole("button", { name: title, exact: true })).toBeVisible();
  }
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
