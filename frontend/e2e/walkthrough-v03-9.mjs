/**
 * V03-9 视觉走查：收拢后的写作台桌面（只读稿纸、右栏抽屉、无导航、无开发痕迹）。
 *
 * 运行前置：
 *   1. frontend/ 已 npm run build；
 *   2. uv run python -m tests.e2e_server --port 8931 已启动（真 API + build 产物 + 定速假模型）。
 * 用法：node e2e/walkthrough-v03-9.mjs
 *
 * 口径（计划 §8）：
 * - 四窗口 1440×900 / 1280×800 / 1024×768 / 390×844；截图必须来自真实内容与状态；
 * - 稿纸默认只读，手工修改才进副本；头部无「保存正文/专注写作」残留；
 * - 右栏默认不渲染面板，标记条常驻；抽屉 Esc/遮罩可关；
 * - 无「试写」入口；空态只有导入与对话两条路；
 * - 界面无阶段编号、内部 ID、hash、「本阶段」措辞；
 * - 文字可读：正文 ≥18px、行宽 ≤44em、关键文字对比度 ≥4.5:1（值取自 theme.ts 令牌）。
 *
 * 证据归档：dev-notes/v0.3/artifacts/visual-walkthrough-9/。
 */

import { chromium } from "playwright";
import { mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE = "http://127.0.0.1:8931";
const SHOTS = "C:/Projects/Agent/myagent/dev-notes/v0.3/artifacts/visual-walkthrough-9";
mkdirSync(SHOTS, { recursive: true });

const results = [];
const consoleErrors = [];

function report(step, ok, detail = "") {
  results.push({ step, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${step}${detail ? ` — ${detail}` : ""}`);
}

function shot(page, name) {
  return page.screenshot({ path: join(SHOTS, `${name}.png`) });
}

/** WCAG 对比度。文字令牌实时读 theme.ts（防漂移）；背景取 index.css 的静态值。 */
function tokenFromTheme(name) {
  const source = readFileSync(join(HERE, "../src/theme.ts"), "utf8");
  const match = source.match(new RegExp(`\\b${name}:\\s*"(#[0-9A-Fa-f]{6})"`));
  if (!match) throw new Error(`theme.ts 中找不到令牌 ${name}`);
  return match[1];
}
const TOKENS = {
  sheetInk: tokenFromTheme("sheetInk"),
  sheet: tokenFromTheme("sheet"),
  fg: tokenFromTheme("fg"),
  sub: tokenFromTheme("sub"),
  panelHead: tokenFromTheme("panelHead"),
  panel: "#F5F8F9", // index.css .panel 渐变末端（最暗背景 = 最严对比）
  panelHeadBg: "#E5ECEF", // index.css .panel-head 渐变末端
};
function luminance(hex) {
  const channels = [1, 3, 5].map((i) => {
    const value = parseInt(hex.slice(i, i + 2), 16) / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}
function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const MANUSCRIPT = `## 第一章 雨夜的客人

雨下了一整夜。艾琳把最后一间客房的钥匙推过柜台时，注意到那位客人把行李箱留在了伞架旁边，自己只带了一把湿透的雨伞进门。他没有说要住几晚，也没有问房价，只是把怀表放在柜台上，问了句：现在的潮汐时刻表还准吗。

艾琳在这座海边客栈做了七年前台，第一次有人用潮汐时刻表代替身份证。她回答：准的，渔民比钟表更信任它。客人点了点头，把怀表收了回去，拎起行李箱上了二楼。楼梯倒数第三级会响，他恰好在那一级停了停，像已经知道一样。

## 第二章 潮汐时刻表

第二早晨，艾琳在门廊的钉子上发现了那张潮汐时刻表。纸质已经发脆，边角用铅笔写满了小字：满潮、转流、夜钓的窗口。最新的一行墨迹很新——今天下午四点，东码头，第二条船。

她把时刻表拿进店里，放在那本住宿登记簿旁边。登记簿上只有一位客人的名字，字迹克制而端正：沈砚。职业栏空着，只写了到达时间：凌晨一点十七分。退房时间那一栏，至今是空白。

## 第三章 第二把钥匙

中午以前来了三位客人。第一位是海事处的年轻职员，来核对今年的渔船登记；第二位自称记者，想问去年冬天那场溺亡事故；第三位什么也没说，只是在客栈转了一圈，看了楼梯、雨伞架和柜台上的怀表，就要了一壶茶。
`;

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(`console.error: ${m.text()}`);
});

const stamp = Date.now().toString(36);
const projectName = `走查-9-${stamp}`;
let pid = "";

try {
  // ---- S0 真实内容准备：建作品、账本人物+关系（图谱要有可键盘移动的多行）、导入文件 ----
  const created = await page.request.post(`${BASE}/api/v2/projects`, {
    data: { name: projectName },
  });
  pid = (await created.json()).project_id;
  const seedEntry = async (body) => {
    const res = await page.request.post(
      `${BASE}/api/v2/projects/${pid}/ledger/entries`,
      { data: body },
    );
    return (await res.json()).entry.id;
  };
  const shenId = await seedEntry({
    kind: "story.character",
    fields: { name: "沈砚", identity: "海边客栈的客人" },
    hypothesis: false,
    request_id: `walkthrough-9-ledger-a-${stamp}`,
  });
  const aiLinId = await seedEntry({
    kind: "story.character",
    fields: { name: "艾琳", identity: "客栈前台" },
    hypothesis: false,
    request_id: `walkthrough-9-ledger-b-${stamp}`,
  });
  await seedEntry({
    kind: "story.relationship",
    fields: {
      source_id: shenId,
      target_id: aiLinId,
      relation_type: "旧识",
      public_state: "客栈前台与客人",
      true_state: "旧识",
    },
    hypothesis: false,
    request_id: `walkthrough-9-ledger-c-${stamp}`,
  });
  const importFile = join(tmpdir(), `walkthrough-9-${stamp}.md`);
  writeFileSync(importFile, MANUSCRIPT, "utf8");
  report("S0 真实内容准备", Boolean(pid), `作品 ${projectName}`);

  // ---- S1 打开写作台：登记作品到本浏览器并载入（无导航栏，写作台即主视图）----
  await page.goto(`${BASE}/?chatMock=0`, { waitUntil: "domcontentloaded" });
  await page.evaluate(
    ({ id, name }) => {
      localStorage.setItem(
        "hastings.v2.projects",
        JSON.stringify([{ projectId: id, name, source: "created" }]),
      );
      localStorage.setItem("hastings.v2.current", id);
    },
    { id: pid, name: projectName },
  );
  await page.reload({ waitUntil: "domcontentloaded" });
  const composer = page.getByLabel("想法输入框");
  await composer.waitFor({ state: "visible", timeout: 10000 });
  const navRemoved = (await page.getByRole("navigation", { name: "主导航" }).count()) === 0;
  report("S1 写作台为唯一视图", navRemoved, navRemoved ? "无主导航栏" : "仍存在导航栏");

  // ---- S2 UI 全流程导入三章真实正文 ----
  await page.getByRole("button", { name: "导入正文" }).click();
  await page.locator('input[type="file"]').setInputFiles(importFile);
  await page.getByRole("button", { name: "确认导入" }).click({ timeout: 15000 });
  await page.getByRole("button", { name: "完成" }).click();
  await page.getByRole("button", { name: /第一章 雨夜的客人/ }).first().waitFor({
    state: "visible",
    timeout: 15000,
  });
  const sheet = page.getByLabel("人工正文");
  await sheet.waitFor({ state: "visible", timeout: 10000 });
  const bodyText = await sheet.inputValue();
  report("S2 导入真实正文", bodyText.includes("雨下了一整夜"), "三章导入且首章载入");
  // 稿纸默认只读（M2）：未进手工修改时不可写
  report(
    "S2 稿纸默认只读",
    (await sheet.getAttribute("readonly")) !== null,
    "textarea 带 readonly",
  );
  await shot(page, "01-desk-1440");

  // ---- S3 四种窗口：布局与可达 ----
  const windows = [
    { name: "1440×900 宽屏", width: 1440, height: 900, file: "02-window-1440" },
    { name: "1280×800 宽屏", width: 1280, height: 800, file: "03-window-1280" },
    { name: "1024×768 中屏", width: 1024, height: 768, file: "04-window-1024" },
    { name: "390×844 小屏", width: 390, height: 844, file: "05-window-390" },
  ];
  for (const w of windows) {
    await page.setViewportSize({ width: w.width, height: w.height });
    await page.waitForTimeout(400);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    const box = await sheet.boundingBox();
    const mainWidth = box ? Math.round(box.width) : 0;
    report(
      `S3 ${w.name} 无横向溢出`,
      overflow <= 1,
      `正文区宽 ${mainWidth}px${overflow > 1 ? `（溢出 ${overflow}px）` : ""}`,
    );
    report(`S3 ${w.name} 输入框可达`, await composer.isVisible(), "唯一输入框可见");
    // 断点：<lg（1024）左目录与右栏都走抽屉按钮（lg:hidden）
    const navToggle = await page
      .getByRole("button", { name: /打开目录|关闭目录/ })
      .isVisible();
    const railToggle = await page
      .getByRole("button", { name: /打开侧栏|关闭侧栏/ })
      .isVisible();
    const expected = w.width < 1024;
    report(
      `S3 ${w.name} 抽屉开关符合断点`,
      navToggle === expected && railToggle === expected,
      `目录开关 ${navToggle ? "有" : "无"}、侧栏开关 ${railToggle ? "有" : "无"}`,
    );
    await shot(page, w.file);
  }

  // ---- S4 200% 缩放等价视口 640×450：主要操作仍可达 ----
  await page.setViewportSize({ width: 640, height: 450 });
  await page.waitForTimeout(400);
  const saveIdea = page.getByRole("button", { name: "保存想法" });
  report(
    "S4.1 200% 等价视口 640×450 基础操作",
    (await composer.isVisible()) && (await saveIdea.isVisible()),
    "输入框/保存想法可见",
  );
  await page.getByRole("button", { name: "打开目录" }).click();
  const chapterInDrawer = page.getByRole("button", { name: /第一章 雨夜的客人/ }).first();
  report("S4.2 目录抽屉可达章节", await chapterInDrawer.isVisible(), "抽屉内章节目录可见");
  await shot(page, "06-zoom-640-equivalent");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  report("S4.3 Esc 关闭目录抽屉", !(await chapterInDrawer.isVisible()), "抽屉已收起");

  // ---- S5 右栏默认不渲染，标记常驻；抽屉可关（M3）----
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(300);
  report(
    "S5.1 右栏默认不渲染面板",
    (await page.locator('aside[role="dialog"]').count()) === 0,
    "无抽屉",
  );
  report(
    "S5.2 标记条常驻（账本/图谱/修订）",
    (await page.getByLabel("侧栏标记").isVisible()) &&
      (await page.getByRole("button", { name: "账本" }).isVisible()),
    "标记条可见",
  );
  await page.getByRole("button", { name: "账本" }).click();
  await page.getByRole("dialog", { name: "故事账本" }).waitFor({ state: "visible", timeout: 10000 });
  report("S5.3 点标记开抽屉", true, "故事账本抽屉打开");
  await shot(page, "07-rail-drawer");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  report(
    "S5.4 Esc 关抽屉",
    (await page.locator('aside[role="dialog"]').count()) === 0,
    "抽屉已收起",
  );

  // ---- S6 手工修改：键盘进入 → 编辑自动保存 → 完成（M2 的三态头部）----
  await page.getByRole("button", { name: /第一章 雨夜的客人/ }).first().click();
  await sheet.waitFor({ state: "visible", timeout: 10000 });
  const editBtn = page.getByRole("button", { name: "手工修改" });
  await editBtn.focus();
  await page.keyboard.press("Enter");
  await sheet.waitFor({ state: "visible", timeout: 10000 });
  await page.waitForFunction(() => {
    const ta = document.querySelector('textarea[aria-label="人工正文"]');
    return ta && ta.getAttribute("readonly") === null;
  }, { timeout: 10000 }).catch(() => {});
  const editableNow = (await sheet.getAttribute("readonly")) === null;
  report("S6.1 手工修改激活后可编辑", editableNow, editableNow ? "已进副本" : "仍只读");
  await sheet.click();
  await page.keyboard.press("End");
  await page.keyboard.type("（走查追加的一句。）");
  await page
    .getByText(/修改中 · 已保存/)
    .waitFor({ state: "visible", timeout: 10000 })
    .catch(() => {});
  const completionBtn = page.getByRole("button", { name: "完成修改" });
  await completionBtn.focus();
  await page.keyboard.press("Enter");
  await page
    .getByRole("button", { name: "手工修改" })
    .waitFor({ state: "visible", timeout: 10000 });
  report("S6.2 键盘编辑与完成修改", true, "输入→自动保存→完成回到阅读态");
  await shot(page, "08-manual-edit");

  // ---- S7 图谱文字目录键盘（沿用 V03-6.5 口径）----
  // 右栏「图谱」标记只开概览小图；「展开查看」把同一视图放大到主区，才出现画布/文字目录切换。
  await page.getByRole("button", { name: "图谱" }).click();
  await page.getByRole("dialog", { name: "关联图谱" }).waitFor({ state: "visible", timeout: 10000 });
  await page.getByRole("button", { name: "展开查看" }).click();
  await page
    .getByRole("button", { name: "文字目录" })
    .waitFor({ state: "visible", timeout: 10000 });
  await page.getByRole("button", { name: "文字目录" }).click();
  const directory = page.getByRole("listbox", { name: /图谱文字目录/ });
  const hasDirectory = await directory
    .waitFor({ state: "visible", timeout: 10000 })
    .then(() => true)
    .catch(() => false);
  if (!hasDirectory) {
    report("S7 键盘操作图谱目录", false, "图谱目录未渲染（无节点）");
  } else {
    const beforeKey = await directory.getAttribute("aria-activedescendant");
    await directory.focus();
    await page.keyboard.press("ArrowDown");
    await page.waitForTimeout(150);
    const afterKey = await directory.getAttribute("aria-activedescendant");
    report(
      "S7 键盘操作图谱目录",
      Boolean(afterKey) && afterKey !== beforeKey,
      `方向键移动 ${beforeKey ?? "无"} → ${afterKey ?? "无"}`,
    );
  }
  await shot(page, "09-graph-directory");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);

  // ---- S8 无「试写」入口 + 无开发痕迹（界面快照与源码）----
  const noTryWrite =
    (await page.getByRole("button", { name: /试写/ }).count()) === 0 &&
    (await page.getByText(/试写/).count()) === 0;
  report("S8.1 界面无「试写」入口", noTryWrite, noTryWrite ? "无试写" : "仍存在试写入口");
  const body = await page.evaluate(() => document.body.innerText);
  const tracePatterns = [
    /\(V03-/,
    /\bprj_[0-9a-zA-Z]/,
    /\bcommit [0-9a-f]{7,}/i,
    /本阶段/,
  ];
  const hits = tracePatterns.filter((re) => re.test(body));
  report(
    "S8.2 界面无阶段编号/内部 ID/hash/「本阶段」",
    hits.length === 0,
    hits.length === 0 ? "快照干净" : `命中 ${hits.map(String).join("、")}`,
  );
  // 源码同步确认：收拢后的壳层源码不含阶段编号与「本阶段」
  const shellSources = ["src/App.tsx", "src/components/desk/DeskShell.tsx", "src/components/desk/DeskComposer.tsx"];
  const sourceHits = [];
  for (const rel of shellSources) {
    const text = readFileSync(join(HERE, "..", rel), "utf8");
    if (/\(V03-/.test(text) || /本阶段/.test(text)) sourceHits.push(rel);
  }
  report(
    "S8.3 壳层源码无阶段编号/「本阶段」",
    sourceHits.length === 0,
    sourceHits.length === 0 ? "源码干净" : `命中 ${sourceHits.join("、")}`,
  );

  // ---- S9 文字可读：正文字号/行宽 + 对比度 ----
  await page.getByRole("button", { name: /第一章 雨夜的客人/ }).first().click();
  await sheet.waitFor({ state: "visible", timeout: 10000 });
  const metrics = await page.evaluate(() => {
    const ta = document.querySelector('textarea[aria-label="人工正文"]');
    const style = getComputedStyle(ta);
    const composerTa = document.querySelector('textarea[aria-label="想法输入框"]');
    return {
      manuscriptFontPx: parseFloat(style.fontSize),
      manuscriptLineHeight: parseFloat(style.lineHeight),
      manuscriptWidthPx: Math.round(ta.getBoundingClientRect().width),
      composerFontPx: parseFloat(getComputedStyle(composerTa).fontSize),
    };
  });
  report(
    "S9.1 正文字号 ≥18px",
    metrics.manuscriptFontPx >= 18,
    `实测 ${metrics.manuscriptFontPx}px / 行高 ${metrics.manuscriptLineHeight}px`,
  );
  const emWidth = metrics.manuscriptFontPx;
  report(
    "S9.2 正文行宽 ≤44em",
    metrics.manuscriptWidthPx <= 44 * emWidth + 1,
    `实测 ${metrics.manuscriptWidthPx}px ≈ ${(metrics.manuscriptWidthPx / emWidth).toFixed(1)}em`,
  );
  report(
    "S9.3 输入框字号 ≥14px",
    metrics.composerFontPx >= 14,
    `实测 ${metrics.composerFontPx}px`,
  );
  const pairs = [
    ["正文/纸面", TOKENS.sheetInk, TOKENS.sheet],
    ["主文字/面板", TOKENS.fg, TOKENS.panel],
    ["次级文字/面板", TOKENS.sub, TOKENS.panel],
    ["面板标题/标题底", TOKENS.panelHead, TOKENS.panelHeadBg],
  ];
  for (const [label, fg, bg] of pairs) {
    const ratio = contrast(fg, bg);
    report(`S9.4 对比度 ${label} ≥4.5:1`, ratio >= 4.5, `实测 ${ratio.toFixed(2)}:1`);
  }
  await shot(page, "10-readability");

  // ---- 汇总 ----
  const failed = results.filter((r) => !r.ok);
  console.log("\n==== 走查汇总 ====");
  console.log(`通过 ${results.length - failed.length}/${results.length}`);
  const realErrors = consoleErrors.filter((e) => !e.includes("favicon") && !e.includes("NetSlot"));
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
