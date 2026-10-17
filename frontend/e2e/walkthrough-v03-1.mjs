/**
 * V03-1 收口走查脚本（完整演示切片 + 零自动重放 + 停止 + 缩放等价视口）。
 *
 * 运行前置：后端 :8000 已启动且 Ollama 可用；vite dev :5173 已启动。
 * 用法：node e2e/walkthrough-v03-1.mjs
 * 说明：?chatMock=0 显式退出模拟（V03-1.5 前端接线新增），全程走真实 API。
 * 200% 缩放以 640×450 等价视口代理（同布局约束）；浏览器本体缩放无 headless 等价物。
 */
import { chromium } from "playwright";

const BASE = "http://localhost:5173/?chatMock=0";
const results = [];
const consoleErrors = [];

function report(step, ok, detail = "") {
  results.push({ step, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${step}${detail ? ` — ${detail}` : ""}`);
}

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(`console.error: ${m.text()}`);
});

try {
  // ---- S1 写作台三部分壳与构思状态 ----
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "写作台" }).click();
  const composer = page.getByLabel("想法输入框");
  await composer.waitFor({ state: "visible", timeout: 10000 });
  report("S1 打开写作台", true, "输入框可见");

  // ---- S2 保存两条想法（无模型调用）----
  const idea1 = "走查想法一：船坞里的第二把钥匙";
  const idea2 = "走查想法二：潮汐时间表与不在场证明";
  for (const [i, text] of [idea1, idea2].entries()) {
    await composer.fill(text);
    await page.getByRole("button", { name: "保存想法" }).click();
    await page
      .getByText(text, { exact: false })
      .first()
      .waitFor({ state: "visible", timeout: 15000 });
    report(`S2.${i + 1} 保存想法`, true, text);
  }
  // 模型状态提示：真实 API 已连接（不出现「模拟模式」）
  const mockBadge = await page.getByText("模拟模式").count();
  report("S2.3 真实 API 模式", mockBadge === 0, mockBadge === 0 ? "无模拟标识" : "出现模拟标识");

  // ---- S3 零自动重放：打开讨论确认只有作者消息，等待若干秒无助手消息 ----
  await page.getByRole("button", { name: `打开讨论：${idea1}` }).click();
  await page.getByLabel("讨论消息").waitFor({ state: "visible", timeout: 10000 });
  await page.waitForTimeout(4000);
  const assistantBefore = await page
    .getByLabel("讨论消息")
    .getByText("回复中…")
    .count();
  report("S3 连接后零自动重放", assistantBefore === 0, "打开讨论 4s 内无模型回复出现");

  // ---- S4 接着讨论：流式回复出现并完成 ----
  // qwen3.5 思考型模型首 token 可延迟 ~2 分钟，整轮 ~5 分钟：超时放宽到 10 分钟。
  await page.getByRole("button", { name: "接着讨论" }).first().click();
  await page
    .getByLabel("讨论消息")
    .getByText("回复中…")
    .first()
    .waitFor({ state: "visible", timeout: 60000 });
  report("S4.1 接着讨论启动", true, "轮次开始流式输出");
  // 等完成：「停止回复」按钮消失（activeTurnId 清空）且不再有流式游标
  await page
    .getByRole("button", { name: "停止回复" })
    .waitFor({ state: "detached", timeout: 600000 });
  report("S4.2 回复完成", true, "轮次进入终态");

  // ---- S5 停止：轮次运行中点停止，部分回复保留 ----
  await page.getByRole("button", { name: "接着讨论" }).first().click();
  await page
    .getByRole("button", { name: "停止回复" })
    .waitFor({ state: "visible", timeout: 60000 });
  await page.getByRole("button", { name: "停止回复" }).click();
  await page
    .getByLabel("讨论消息")
    .getByText("以上是已收到的部分回复。")
    .first()
    .waitFor({ state: "visible", timeout: 30000 });
  report("S5 停止回复", true, "显示「已收到的部分回复」（0 字停止合法，文本冻结为准）");

  // ---- S6 草稿刷新恢复 ----
  await composer.fill("刷新前未保存的草稿文字走查标记");
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "写作台" }).click();
  const restored = await page.getByLabel("想法输入框").inputValue();
  report("S6 草稿刷新恢复", restored.includes("走查标记"), restored.slice(0, 30));

  // ---- S7 200% 缩放等价视口：主要操作仍可达 ----
  await page.setViewportSize({ width: 640, height: 450 });
  await page.waitForTimeout(500);
  const composerVisible = await page.getByLabel("想法输入框").isVisible();
  const saveVisible = await page
    .getByRole("button", { name: "保存想法" })
    .isVisible();
  report("S7 缩放等价视口(640×450)", composerVisible && saveVisible, "输入框与保存按钮可达");

  // ---- 汇总 ----
  const failed = results.filter((r) => !r.ok);
  console.log("\n==== 走查汇总 ====");
  console.log(`通过 ${results.length - failed.length}/${results.length}`);
  const realErrors = consoleErrors.filter(
    (e) => !e.includes("favicon") && !e.includes("NetSlot"),
  );
  if (realErrors.length > 0) {
    console.log("控制台错误:");
    for (const e of realErrors) console.log(`  ${e}`);
  } else {
    console.log("控制台无 error");
  }
  process.exitCode = failed.length === 0 && realErrors.length === 0 ? 0 : 1;
} finally {
  await browser.close();
}
