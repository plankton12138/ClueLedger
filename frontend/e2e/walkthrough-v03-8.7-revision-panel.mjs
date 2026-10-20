/**
 * V03-8.7 整体调整面板走查（真 uvicorn + DeepSeek）。
 *
 * 走的是作者真实路径：写作台 → 打开作品与第一章 → 整体调整 → 开任务（预算 1）
 * → 执行一轮（预算停在第 2 章）→ 继续 → 执行一轮 → 一次发布。
 *
 * 断言只看可观察结果：预算「已用/上限」、等待提示、暂存章数、发布后正文换版。
 * 截图归档 dev-notes/v0.3/artifacts/revision-panel/。
 */

import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.WB_BASE ?? "http://127.0.0.1:8941";
const SHOTS = "C:/Projects/Agent/myagent/dev-notes/v0.3/artifacts/revision-panel";
mkdirSync(SHOTS, { recursive: true });

const stamp = Date.now().toString(36);
let passed = 0;
let failed = 0;

function report(name, ok, detail) {
  if (ok) passed += 1;
  else failed += 1;
  console.log(`${ok ? "[PASS]" : "[FAIL]"} ${name}: ${detail}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const consoleErrors = [];
page.on("pageerror", (e) => consoleErrors.push(e.message));
page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(m.text());
});

async function shot(name) {
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: false });
}

try {
  // ---- 准备：作品 + 两章正文（API，与作者导入同一路径）----
  const created = await page.request.post(`${BASE}/api/v2/projects`, {
    data: { name: `面板走查-${stamp}` },
  });
  const pid = (await created.json()).project_id;
  const first = await page.request.post(`${BASE}/api/v2/projects/${pid}/chapters/import`, {
    data: {
      idempotency_key: `panel-1-${stamp}`,
      title: "第一章 九点的钟声",
      text: "九点整，钟停了。林昭把伞靠在门边，雪水顺着伞骨滴在石板上。柜台后没有人。",
    },
  });
  const second = await page.request.post(`${BASE}/api/v2/projects/${pid}/chapters/import`, {
    data: {
      idempotency_key: `panel-2-${stamp}`,
      title: "第二章 空房",
      text: "二楼尽头有一扇没有门牌的门。门后没有声音，只有一股冷气贴着地板流出来。",
    },
  });
  const firstId = (await first.json()).chapter_id;
  const secondId = (await second.json()).chapter_id;
  const before = {};
  for (const cid of [firstId, secondId]) {
    const body = await (await page.request.get(`${BASE}/api/v2/projects/${pid}/chapters/${cid}`)).json();
    before[cid] = body.text;
  }

  // ---- 打开写作台并载入该作品 ----
  await page.goto(`${BASE}/?chatMock=0`, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "写作台" }).click();
  await page.getByRole("combobox", { name: "选择项目" }).click();
  await page.getByRole("option", { name: "管理 / 新建项目…" }).click();
  await page.getByLabel("已有项目编号").fill(pid);
  await page.getByRole("button", { name: "载入项目" }).click();
  await page.getByLabel("想法输入框").waitFor({ state: "visible", timeout: 15000 });

  // ---- 打开第一章，进入整体调整 ----
  await page.getByRole("button", { name: /第一章 九点的钟声/ }).first().click();
  await page.getByRole("button", { name: "整体调整" }).click();
  await page.getByRole("region", { name: "整体调整" }).waitFor({ timeout: 10000 });
  report("S1 整体调整入口可达", true, "写作台头部按钮 → 主区面板");

  // ---- 开任务：预算 1 ----
  await page.getByLabel("整体调整要求").fill("把这一章改得更克制，保留全部事实与时间线。");
  const budget = page.getByLabel("调用预算");
  await budget.fill("1");
  await page.getByRole("button", { name: "开始整体调整" }).click();
  await page.getByText("0 / 1 次").waitFor({ timeout: 10000 });
  await page.getByText("0 / 2 章").waitFor({ timeout: 10000 });
  report("S2 开任务零调用", true, "预算 0/1、覆盖 0/2，暂存 0 章");
  await shot("01-task-started");

  // ---- 执行一轮：预算 1 只够一章 ----
  await page.getByRole("button", { name: /执行一轮/ }).click();
  await page.getByText(/预算用完，停在「第二章 空房」之前/).waitFor({ timeout: 180000 });
  await page.getByRole("button", { name: "继续" }).waitFor({ timeout: 10000 });
  await page.getByText("1 / 1 次").waitFor({ timeout: 10000 });
  const rows = await page.getByRole("checkbox").count();
  report(
    "S3 预算停在第二章前",
    rows === 2,
    `已用 1/1、完成 1 章、章节行 ${rows}、提示可继续`,
  );
  await shot("02-budget-stop");

  // ---- 继续 + 再跑一轮 ----
  await page.getByRole("button", { name: "继续" }).click();
  await page.getByText("已追加预算").waitFor({ timeout: 10000 });
  await page.getByRole("button", { name: /执行一轮/ }).click();
  await page.getByText(/本轮完成 1 章/).waitFor({ timeout: 180000 });
  await page.getByText("2 / 2 次").waitFor({ timeout: 10000 });
  report("S4 继续后跑完剩余章", true, "已用 2/2、暂存 2 章");
  await shot("03-executed");

  // ---- 一次发布 ----
  await page.getByRole("button", { name: /发布（2 章）/ }).click();
  await page.getByText(/已发布 2 章/).waitFor({ timeout: 60000 });
  report("S5 一次发布", true, "正文与账本进入同一个版本");

  const after = {};
  for (const cid of [firstId, secondId]) {
    const body = await (await page.request.get(`${BASE}/api/v2/projects/${pid}/chapters/${cid}`)).json();
    after[cid] = body.text;
  }
  const changed = [firstId, secondId].filter((cid) => after[cid] !== before[cid]);
  report("S6 两章正文都换版", changed.length === 2, `实际换版 ${changed.length} 章`);

  const task = await (await page.request.get(`${BASE}/api/v2/projects/${pid}/revision-tasks/current`)).json();
  report(
    "S7 任务完成且预算如实",
    task.state === "completed" && task.budget.calls_used === 2 && task.budget.call_limit === 2,
    `state=${task.state}、calls_used=${task.budget.calls_used}/${task.budget.call_limit}`,
  );
  await shot("04-published");

  report("S8 控制台无 error", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | ") || "无");
} catch (error) {
  report("走查中断", false, String(error).slice(0, 400));
  await shot("99-failure");
} finally {
  console.log(`\n结果：${passed} 通过 / ${failed} 失败`);
  await browser.close();
}
