import { test, expect } from "./fixtures/frontendExperience";
import type { Page } from "@playwright/test";
import { mkdirSync, readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { build } from "vite";
import react from "@vitejs/plugin-react";

test("控件完整状态与键盘浮层行为", async ({ page }) => {
  const result = await build({ configFile: false, root: process.cwd(), logLevel: "error", plugins: [react()],
    build: { write: false, rollupOptions: { input: resolve("e2e/fixtures/ui-foundation.tsx"), output: { format: "iife", inlineDynamicImports: true } } } });
  if (Array.isArray(result) || !("output" in result)) throw new Error("控件夹具没有构建产物");
  const bundle = result.output.find(output => output.type === "chunk");
  if (!bundle || bundle.type !== "chunk") throw new Error("控件夹具缺少脚本");
  const assets = resolve(process.env.HASTINGS_E2E_STATIC_DIR ?? "dist", "assets");
  const css = readFileSync(resolve(assets, readdirSync(assets).find(name => name.endsWith(".css"))!), "utf8");
  await page.goto("about:blank"); await page.setContent('<div id="root"></div>');
  await page.addStyleTag({ content: css }); await page.addScriptTag({ content: bundle.code });
  await expect(page.getByRole("button", { name: "发布", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "处理中", exact: false })).toHaveAttribute("aria-busy", "true");
  await expect(page.getByLabel("名称", { exact: true })).toHaveAttribute("aria-invalid", "true");
  await page.getByRole("button", { name: "条目 摘要随当前行状态显示", exact: true }).click();
  await expect(page.getByRole("button", { name: "条目 摘要随当前行状态显示", exact: true })).toHaveAttribute("aria-current", "true");
  await page.getByRole("tab", { name: "正文", exact: true }).focus(); await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "历史", exact: true })).toHaveAttribute("aria-selected", "true");
  const trigger = page.getByRole("button", { name: "打开编辑", exact: true }); await trigger.click();
  const dialog = page.getByRole("dialog", { name: "编辑条目", exact: true });
  await expect(dialog).toBeVisible();
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press("Tab");
    expect(await dialog.evaluate(el => el.contains(document.activeElement))).toBe(true);
  }
  await page.keyboard.press("Escape"); await expect(dialog).toBeHidden(); await expect(trigger).toBeFocused();
  await shot(page, "control-states-1440");
});

async function shot(page: Page, name: string) {
  const path = resolve(process.env.HASTINGS_E2E_SCREENSHOT_DIR ?? "../dev-notes/v0.8/artifacts/frontend-experience/screenshots");
  mkdirSync(path, { recursive: true }); await page.screenshot({ path: `${path}/H2-${name}.png` });
}
async function chooseChapter(page: Page) {
  const directory = page.getByRole("button", { name: "目录", exact: true });
  if (await directory.getAttribute("aria-pressed") === "false") await directory.click();
  await page.getByRole("button", { name: "第一章 雨夜", exact: true }).click();
  await expect(page.getByRole("region", { name: "正文阅读", exact: true })).toContainText("侦探抵达港口");
}
async function font(page: Page, scale: number) {
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "设置", exact: true });
  await dialog.getByLabel("调整字体大小").selectOption(String(scale));
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
}

test("窗口与字体矩阵：正文、目录、聊天与草稿都可到达，自动适配不改栏宽偏好", async ({ page, book }) => {
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await chooseChapter(page);
  const paper = page.getByRole("region", { name: "正文阅读", exact: true });
  const widths = [1440, 1024, 760, 640];
  for (const width of widths) for (const scale of [100, 150]) {
    await page.setViewportSize({ width, height: width === 1024 ? 768 : width === 640 ? 800 : 900 });
    await font(page, scale);
    await expect(paper).toBeVisible();
    await expect(paper).toContainText("侦探抵达港口");
    const box = await paper.boundingBox();
    expect(box!.width).toBeGreaterThanOrEqual(width === 1440 ? 560 : width === 1024 && scale === 100 ? 420 : width - 30);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await shot(page, `writing-${width}-${scale}`);
    const layout = await page.locator(".writing-layout").getAttribute("data-layout");
    if (width === 1024 && scale === 100) expect(layout).toBe("two");
    if (width === 640 || width === 760) expect(layout).toBe("single");
    if (layout !== "three" && layout !== "reading") {
      await expect(page.getByRole("separator", { name: "目录栏宽度", exact: true })).toHaveCount(0);
      const trigger = page.getByRole("button", { name: "目录", exact: true });
      await trigger.click();
      const directory = page.getByRole("region", { name: "目录", exact: true });
      await expect(directory.getByRole("navigation", { name: "作品目录", exact: true })).toBeVisible();
      await page.keyboard.press("Escape"); await expect(directory).toBeHidden(); await expect(trigger).toBeFocused();
    }
    if (layout === "single") {
      await page.getByRole("button", { name: "对话", exact: true }).click();
      await expect(page.locator(".chat-input")).toBeVisible();
      await page.locator(".chat-input").fill("布局切换保留的聊天草稿");
      const send = await page.getByRole("button", { name: "发送", exact: true }).boundingBox();
      expect(send!.x + send!.width).toBeLessThanOrEqual(width);
      expect(send!.y + send!.height).toBeLessThanOrEqual(width === 640 ? 800 : width === 1024 ? 768 : 900);
      await page.getByRole("button", { name: "对话", exact: true }).click();
      await expect(paper).toBeVisible();
      await page.getByRole("button", { name: "对话", exact: true }).click();
      await expect(page.locator(".chat-input")).toHaveValue("布局切换保留的聊天草稿");
      await page.getByRole("button", { name: "对话", exact: true }).click();
    }
    const prefs = await page.evaluate(() => JSON.parse(localStorage.getItem("hastings.workspace.layout")!));
    expect(prefs).toMatchObject({ sidebarWidth: 240, chatWidth: 420, chatVisible: true, sidebarVisible: true });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await shot(page, "writing-390-150-observation");
});

test("目录和对话是固定位置的独立开关，关闭对话不展开目录", async ({ page, book }) => {
  await page.goto(`/#/workspaces/${book.id}/writing`);
  const directory = page.getByRole("button", { name: "目录", exact: true });
  const chat = page.getByRole("button", { name: "对话", exact: true });
  const pane = page.getByRole("region", { name: "目录", exact: true });
  for (const width of [1440, 1024, 640]) for (const scale of [100, 150]) {
    await page.setViewportSize({ width, height: width === 1024 ? 768 : width === 640 ? 800 : 900 });
    await font(page, scale);
    // 先关闭目录，之后开关对话都不应改变目录或工具顺序。
    if (await directory.getAttribute("aria-pressed") === "true") await directory.click();
    await expect(pane).toBeHidden();
    const before = { directory: await directory.boundingBox(), chat: await chat.boundingBox() };
    for (let n = 0; n < 2; n++) {
      const wasOpen = await chat.getAttribute("aria-pressed") === "true";
      await chat.click();
      await expect(chat).toHaveAttribute("aria-pressed", String(!wasOpen));
      await expect(pane).toBeHidden();
      await expect(directory).toHaveAttribute("aria-pressed", "false");
      expect(await directory.boundingBox()).toEqual(before.directory);
      expect(await chat.boundingBox()).toEqual(before.chat);
      await expect(page.getByRole("button", { name: "正文", exact: true })).toHaveCount(0);
    }
    const chatState = await chat.getAttribute("aria-pressed");
    await directory.click(); await expect(pane).toBeVisible();
    await expect(directory).toHaveAttribute("aria-pressed", "true");
    await expect(chat).toHaveAttribute("aria-pressed", chatState!);
    await chat.click(); await expect(pane).toBeVisible();
    await expect(directory).toHaveAttribute("aria-pressed", "true");
    await chat.click(); await expect(pane).toBeVisible();
    // 同一个目录按钮能够直接关闭目录，工具栏不会被浮层遮住。
    await directory.click(); await expect(pane).toBeHidden();
    await expect(chat).toHaveAttribute("aria-pressed", chatState!);
  }
  await page.setViewportSize({ width: 1024, height: 768 }); await font(page, 100);
  if (await chat.getAttribute("aria-pressed") === "true") await chat.click();
  await expect(pane).toBeHidden(); await expect(directory).toBeVisible();
  await expect(page.getByRole("region", { name: "正文阅读", exact: true })).toBeVisible();
  const output = resolve("../data/test-tmp/writing-toggle-buttons-1024.png");
  mkdirSync(resolve("../data/test-tmp"), { recursive: true }); await page.screenshot({ path: output });
});

test("模块库精确链接、前进后退、作品内同一入口与未登记上下文", async ({ page, book }) => {
  await page.goto(`/#/workspaces/${book.id}/writing`); await chooseChapter(page);
  await page.locator(".paper-sheet").evaluate(el => { el.scrollTop = 500; });
  const scroll = await page.locator(".paper-sheet").evaluate(el => el.scrollTop);
  await page.getByRole("button", { name: "添加模块", exact: true }).click();
  await expect(page).toHaveURL(/#\/library/);
  const library = page.getByRole("region", { name: "模块库", exact: true });
  await library.getByLabel("搜索模块").fill(book.id); await library.getByLabel("搜索模块").press("Enter");
  await library.getByRole("button", { name: new RegExp(`长记录-${book.id}`) }).click();
  await expect(page).toHaveURL(/\/revisions\//);
  await library.getByRole("button", { name: "编辑", exact: true }).click();
  await library.getByLabel("模块正文").last().fill("前进后退保留的模块草稿");
  await page.getByRole("link", { name: "写作", exact: true }).click();
  await expect(page.locator(".paper-sheet")).toContainText("侦探抵达港口");
  expect(await page.locator(".paper-sheet").evaluate(el => el.scrollTop)).toBe(scroll);
  await page.goBack();
  await library.getByRole("button", { name: "继续编辑", exact: true }).click();
  await expect(library.getByLabel("模块正文").last()).toHaveValue("前进后退保留的模块草稿");
  await page.goForward(); await expect(page.getByRole("region", { name: "正文阅读", exact: true })).toBeVisible();
  // hash切换保持同一应用文档，不能用整页重载验证会话草稿。
  await page.evaluate(url => { location.hash = url; }, `#/library/modules/${book.moduleId}/revisions/${book.revisionId}?workspace=not_registered`);
  await expect(library.locator(".module-preview")).toContainText(book.body);
  await expect(page).not.toHaveURL(/not_registered/);
  await expect(library.getByRole("button", { name: "添加到作品", exact: true })).toBeDisabled();
});

test("未打开作品仍能读取和发布库内容，应用动作说明缺少作品", async ({ page, book, request }) => {
  await page.route("**/api/file-workspaces", async route => { if (route.request().method() === "GET") await route.fulfill({ json: [] }); else await route.continue(); });
  await page.goto(`/#/library/modules/${book.moduleId}/revisions/${book.revisionId}`);
  const library = page.getByRole("region", { name: "模块库", exact: true });
  await expect(library.locator(".module-preview")).toContainText(book.body);
  await expect(library.getByRole("button", { name: "添加到作品", exact: true })).toHaveAttribute("title", "先打开一个作品");
  await expect(library.getByRole("button", { name: "添加到作品", exact: true })).toBeDisabled();
  // 先转到head，历史版本不直接覆盖。
  const modules = await request.get("/api/module-library/modules?limit=50").then(r => r.json());
  const head = modules.items.find((item: { module_id: string }) => item.module_id === book.moduleId).head;
  await library.locator(".library-detail-toolbar > .library-more > summary").click();
  await library.getByLabel("模块版本", { exact: true }).selectOption(head);
  await library.getByRole("button", { name: "编辑", exact: true }).click();
  await library.getByLabel("模块正文").last().fill("没有作品时保存的全局资料");
  await library.getByRole("button", { name: "保存", exact: true }).click();
  await expect(library.getByText("已保存到模块库；其他作品保持原版本。", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/#\/library/);
});
