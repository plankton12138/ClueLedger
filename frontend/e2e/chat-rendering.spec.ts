import { test, expect, type Page } from "@playwright/test";
import { build } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

let script = "";
let styles = "";

test.beforeAll(async () => {
  const root = fileURLToPath(new URL("..", import.meta.url));
  const result = await build({
    configFile: false, root, logLevel: "error",
    plugins: [{
      name: "chat-render-counts", enforce: "pre",
      transform(code, id) {
        // 只在测试内统计真正发生的 Markdown 解析与轮次渲染，产品没有调试开关。
        if (id.endsWith("/ChatMarkdown.tsx")) return code
          .replace("urlTransform: safeUrl", "urlTransform: (window.__renderStats.markdown++, safeUrl)")
          .replace("urlTransform={safeUrl}", "urlTransform={(window.__renderStats.markdown++, safeUrl)}");
        if (id.endsWith("/ChatTurnView.tsx")) return code.replace("const result = turn.result;",
          "window.__renderStats.turns[turn.id] = (window.__renderStats.turns[turn.id] || 0) + 1; const result = turn.result;");
      },
    }, react()],
    build: { write: false, rollupOptions: { input: resolve(root, "e2e/fixtures/chat-rendering.tsx"), output: { format: "iife", inlineDynamicImports: true } } },
  });
  if (Array.isArray(result) || !("output" in result)) throw new Error("浏览器夹具构建没有返回文件");
  const bundle = result.output.find(output => output.type === "chunk");
  if (!bundle || bundle.type !== "chunk") throw new Error("浏览器夹具缺少脚本");
  script = bundle.code;
  const assets = resolve(process.env.HASTINGS_E2E_STATIC_DIR ?? resolve(root, "dist"), "assets");
  styles = readFileSync(resolve(assets, readdirSync(assets).find(name => name.endsWith(".css"))!), "utf8");
});

async function open(page: Page) {
  await page.goto("about:blank");
  await page.setContent('<div id="root" style="height:100vh;width:100%"></div>');
  await page.addStyleTag({ content: styles });
  await page.addScriptTag({ content: script });
  await page.waitForFunction(() => window.chatFixture?.ready && document.querySelectorAll(".chat-thinking").length === 3);
}

test("长思考折叠时不解析正文，突发分片合批且历史轮次不重绘", async ({ page }) => {
  await open(page);
  await expect(page.locator(".chat-thinking-body")).toHaveCount(0);
  const before = await page.evaluate(() => structuredClone(window.__renderStats));
  expect(before.markdown).toBe(0);
  await page.evaluate(() => window.chatFixture.push(200));
  await expect.poll(() => page.evaluate(() => window.__renderStats.turns.t_live)).toBe(before.turns.t_live + 1);
  const after = await page.evaluate(() => structuredClone(window.__renderStats));
  expect(after.markdown).toBe(0);
  expect(after.turns.t_history1).toBe(before.turns.t_history1);
  expect(after.turns.t_history2).toBe(before.turns.t_history2);
  await page.evaluate(() => window.chatFixture.finish());
  await expect(page.locator(".chat-status")).toHaveCount(0);
  await page.locator(".chat-thinking summary").last().click();
  await expect(page.locator(".chat-thinking-body")).toHaveCount(1);
  expect(await page.locator(".chat-thinking-body").innerText()).toContain("新片段200");
  await expect.poll(() => page.evaluate(() => window.__renderStats.markdown)).toBe(1);
});

test.describe("高像素密度下阅读长思考", () => {
  test.use({ viewport: { width: 424, height: 619 }, deviceScaleFactor: 2 });
  test("展开后完整保留实时原文，向上阅读与选字不被新内容拉走", async ({ page }) => {
    await open(page);
    const thought = page.locator(".chat-thinking").last();
    await thought.locator("summary").click();
    await expect(thought.locator(".chat-thinking-text")).toBeVisible();
    expect(await thought.locator(".chat-thinking-text").textContent()).toBe(await page.evaluate(() => window.chatFixture.text()));
    const position = await page.locator(".chat-scroll").evaluate(element => {
      element.scrollTop = element.scrollHeight - element.clientHeight - 400;
      const text = element.querySelector(".chat-thinking-text")!.firstChild!;
      const range = document.createRange();
      range.setStart(text, 0); range.setEnd(text, 10);
      window.getSelection()!.removeAllRanges(); window.getSelection()!.addRange(range);
      return element.scrollTop;
    });
    await expect(page.getByRole("button", { name: "回到最新" })).toBeVisible();
    await page.evaluate(() => window.chatFixture.push(200));
    await expect.poll(() => thought.locator(".chat-thinking-text").textContent()).toContain("新片段200");
    expect(await page.locator(".chat-scroll").evaluate(element => element.scrollTop)).toBeCloseTo(position, 0);
    expect(await page.evaluate(() => window.__renderStats.markdown)).toBe(0);
    await page.evaluate(() => window.getSelection()!.removeAllRanges());
    await page.getByRole("button", { name: "回到最新" }).click();
    await expect.poll(() => page.locator(".chat-scroll").evaluate(element => element.scrollHeight - element.clientHeight - element.scrollTop)).toBeLessThan(2);
  });
});

test("切换作品后，待刷新的旧分片不能重新填回页面", async ({ page }) => {
  await open(page);
  await page.evaluate(() => { window.chatFixture.push(200); window.chatFixture.reset(); });
  await expect(page.locator(".chat-turn")).toHaveCount(0);
  // 超过展示合批间隔，确保验证的是迟到的定时回调。
  await page.waitForTimeout(100);
  await expect(page.locator(".chat-turn")).toHaveCount(0);
});
