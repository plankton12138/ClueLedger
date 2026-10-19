/**
 * V03-8.5 视觉走查：四种窗口 + 200% 缩放 + 键盘 + 中文输入法 + 无伪入口 + 文字可读。
 *
 * 运行前置：
 *   1. frontend/ 已 npm run build；
 *   2. uv run python -m tests.e2e_server --port 8931 已启动（真 API + build 产物 + 定速假模型）。
 * 用法：node e2e/walkthrough-v03-8.5.mjs
 *
 * 口径（执行指导 §12.3 / 计划 M14）：
 * - 四种窗口 1440×900 / 1280×800 / 1024×768 / 390×844；截图必须来自真实内容与状态；
 * - 200% 缩放以 640×450 等价视口代理（浏览器本体缩放无 headless 等价物，同 V03-1 走查口径）；
 * - 键盘可达编辑/完成/停止/历史/来源/图谱目录，焦点可见；
 * - 中文输入法组合保护：组合期 Enter 不提交、确认后文字完整、Ctrl+Enter 才保存；
 * - 无伪入口：不出现「阶段已交付却还点进占位视图」的入口；
 * - 文字可读：正文 ≥18px、行宽 ≤44em、关键文字对比度 ≥4.5:1（值取自 theme.ts 令牌）。
 *
 * 证据归档：dev-notes/v0.3/artifacts/visual-walkthrough-8.5/。
 */

import { chromium } from "playwright";
import { mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE = "http://127.0.0.1:8931";
const SHOTS = "C:/Projects/Agent/myagent/dev-notes/v0.3/artifacts/visual-walkthrough-8.5";
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

艾琳给第三位客人倒茶的时候，发现他握杯的姿势和沈砚一样——拇指压在杯沿，像在给一枚硬币看正反。她想起昨晚沈砚问的话，忽然明白：这座客栈里问潮汐的人，比问房价的人更清楚彼此为什么而来。
`;

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(`console.error: ${m.text()}`);
});

const stamp = Date.now().toString(36);
const projectName = `走查-8-5-${stamp}`;
let pid = "";

try {
  // ---- S0 真实内容准备：建作品、账本人物+关系（图谱目录要有可键盘移动的多行）、导入文件 ----
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
    request_id: `walkthrough-8-5-ledger-a-${stamp}`,
  });
  const aiLinId = await seedEntry({
    kind: "story.character",
    fields: { name: "艾琳", identity: "客栈前台" },
    hypothesis: false,
    request_id: `walkthrough-8-5-ledger-b-${stamp}`,
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
    request_id: `walkthrough-8-5-ledger-c-${stamp}`,
  });
  const importFile = join(tmpdir(), `walkthrough-8-5-${stamp}.md`);
  writeFileSync(importFile, MANUSCRIPT, "utf8");
  report("S0 真实内容准备", Boolean(pid), `作品 ${projectName}`);

  // ---- S1 打开写作台并按编号载入该作品（项目登记在本浏览器，无服务端列表端点）----
  await page.goto(`${BASE}/?chatMock=0`, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "写作台" }).click();
  await page.getByRole("combobox", { name: "选择项目" }).click();
  await page.getByRole("option", { name: "管理 / 新建项目…" }).click();
  await page.getByLabel("已有项目编号").fill(pid);
  await page.getByRole("button", { name: "载入项目" }).click();
  const composer = page.getByLabel("想法输入框");
  await composer.waitFor({ state: "visible", timeout: 10000 });
  report("S1 打开写作台", true, "唯一输入框可见");

  // ---- S2 UI 全流程导入三章真实正文 ----
  await page.getByRole("button", { name: "导入正文" }).click();
  await page.locator('input[type="file"]').setInputFiles(importFile);
  await page.getByRole("button", { name: "确认导入" }).click({ timeout: 15000 });
  // 对话框开着时外部内容对可访问性树隐藏：先关对话框再查章节目录
  await page.getByRole("button", { name: "完成" }).click();
  await page.getByRole("button", { name: /第一章 雨夜的客人/ }).first().waitFor({
    state: "visible",
    timeout: 15000,
  });
  await page.getByLabel("人工正文").waitFor({ state: "visible", timeout: 10000 });
  const bodyText = await page.getByLabel("人工正文").inputValue();
  report("S2 导入真实正文", bodyText.includes("雨下了一整夜"), "三章导入且首章载入");
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
    const sheet = await page.getByLabel("人工正文").boundingBox();
    const mainWidth = sheet ? Math.round(sheet.width) : 0;
    report(
      `S3 ${w.name} 无横向溢出`,
      overflow <= 1,
      `正文区宽 ${mainWidth}px${overflow > 1 ? `（溢出 ${overflow}px）` : ""}`,
    );
    report(`S3 ${w.name} 输入框可达`, await composer.isVisible(), "唯一输入框可见");
    // 断点：<1024 左导航走抽屉，<1280 右栏走抽屉（M14：不按比例缩三列）
    const navToggle = await page
      .getByRole("button", { name: /打开目录|关闭目录/ })
      .isVisible();
    const railToggle = await page
      .getByRole("button", { name: /打开侧栏|关闭侧栏/ })
      .isVisible();
    report(
      `S3 ${w.name} 抽屉开关符合断点`,
      navToggle === w.width < 1024 && railToggle === w.width < 1280,
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
  // 小屏主区优先：章节经目录抽屉到达
  await page.getByRole("button", { name: "打开目录" }).click();
  const chapterInDrawer = page.getByRole("button", { name: /第一章 雨夜的客人/ }).first();
  report("S4.2 目录抽屉可达章节", await chapterInDrawer.isVisible(), "抽屉内章节目录可见");
  await shot(page, "06-zoom-640-equivalent");
  // Esc 关抽屉
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  report("S4.3 Esc 关闭目录抽屉", !(await chapterInDrawer.isVisible()), "抽屉已收起");

  // ---- S5 键盘：编辑→保存、专注写作、图谱目录、停止 ----
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(300);
  // S5.1 编辑与保存正文（键盘输入 + 回车激活按钮）
  const sheet = page.getByLabel("人工正文");
  await sheet.click();
  await page.keyboard.press("End");
  await page.keyboard.type("（走查追加的一句。）");
  await expectPill(page, "未保存");
  const saveChapter = page.getByRole("button", { name: "保存正文" });
  await saveChapter.focus();
  await page.keyboard.press("Enter");
  await expectPill(page, "已保存");
  report("S5.1 键盘编辑与保存正文", true, "输入→回车保存→状态回到已保存");

  // S5.2 专注写作开关（键盘激活，两侧收起）
  const focusBtn = page.getByRole("button", { name: "专注写作" });
  await focusBtn.focus();
  await page.keyboard.press("Enter");
  await page.waitForTimeout(200);
  const deskAsides = page.locator('main[aria-label="写作台"] aside');
  const asideCount = await deskAsides.count();
  const asideVisible = asideCount > 0 && (await deskAsides.first().isVisible());
  report("S5.2 键盘切换专注写作", !asideVisible, `写作台两侧已收起（aside ${asideCount} 个）`);
  await shot(page, "07-focus-mode");
  await page.getByRole("button", { name: "退出专注写作" }).focus();
  await page.keyboard.press("Enter");
  await page.waitForTimeout(200);

  // S5.3 图谱文字目录（方向键移动 + 回车查看）
  await page.getByRole("button", { name: /关联图谱/ }).first().click();
  // 主区图谱默认画布；切到文字目录再验键盘（查看方式偏好持久化，显式切换）
  await page.getByRole("button", { name: "文字目录" }).click();
  const directory = page.getByRole("listbox", { name: /图谱文字目录/ });
  const hasDirectory = await directory
    .waitFor({ state: "visible", timeout: 10000 })
    .then(() => true)
    .catch(() => false);
  if (!hasDirectory) {
    report("S5.3 键盘操作图谱目录", false, "图谱目录未渲染（无节点）");
  } else {
    const beforeKey = await directory.getAttribute("aria-activedescendant");
    await directory.focus();
    await page.keyboard.press("ArrowDown");
    await page.waitForTimeout(150);
    const afterKey = await directory.getAttribute("aria-activedescendant");
    report(
      "S5.3 键盘操作图谱目录",
      Boolean(afterKey) && afterKey !== beforeKey,
      `方向键移动 ${beforeKey ?? "无"} → ${afterKey ?? "无"}`,
    );
  }
  await shot(page, "08-graph-directory");
  await page.getByRole("button", { name: /第一章 雨夜的客人/ }).first().click();

  // S5.4 保存想法 → 接着讨论 → 键盘停止（部分回复保留）
  await composer.fill("走查想法：潮汐时刻表上的第二行字");
  await page.getByRole("button", { name: "保存想法" }).click();
  await page.getByRole("button", { name: "接着讨论" }).first().click();
  const stopBtn = page.getByRole("button", { name: "停止回复" });
  await stopBtn.waitFor({ state: "visible", timeout: 20000 });
  await stopBtn.focus();
  await page.keyboard.press("Enter");
  const partial = await page
    .getByText("以上是已收到的部分回复。")
    .waitFor({ state: "visible", timeout: 15000 })
    .then(() => true)
    .catch(() => false);
  report("S5.4 键盘停止回复", partial, partial ? "部分回复保留并标记" : "本轮在点击前已完成（定速假模型窗口窄）");

  // ---- S6 中文输入法组合保护 ----
  await page.getByRole("button", { name: /第一章 雨夜的客人/ }).first().click();
  await composer.click();
  await composer.fill("");
  const ideasBefore = await page.getByRole("button", { name: /^打开讨论：/ }).count();
  // 组合期 Enter 不提交（IME 确认键）：派发 isComposing 的 Enter
  await page.keyboard.type("hanshurun");
  await page.evaluate(() => {
    const ta = document.querySelector('textarea[aria-label="想法输入框"]');
    ta.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Enter", code: "Enter", isComposing: true, bubbles: true }),
    );
  });
  const ideasAfterEnter = await page.getByRole("button", { name: /^打开讨论：/ }).count();
  const draftKept = await composer.inputValue();
  report(
    "S6.1 组合期 Enter 不提交",
    ideasAfterEnter === ideasBefore && draftKept.includes("hanshurun"),
    `想法数 ${ideasBefore}→${ideasAfterEnter}，草稿保留「${draftKept}」`,
  );
  // 中文经输入法落入：insertText 走与 IME 提交同一路径
  await page.keyboard.press("End");
  await composer.press("Control+a");
  await page.keyboard.insertText("寒天数客");
  const imeValue = await composer.inputValue();
  report("S6.2 中文输入落入输入框", imeValue === "寒天数客", `当前值：${imeValue}`);
  // Ctrl+Enter 才保存
  await composer.press("Control+Enter");
  await page.getByRole("button", { name: /打开讨论：寒天数客/ }).waitFor({
    state: "visible",
    timeout: 15000,
  });
  report("S6.3 Ctrl+Enter 保存中文想法", true, "列表出现中文条目");
  // 草稿刷新恢复（localStorage）
  await composer.fill("刷新前的拼音草稿 hányè");
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "写作台" }).click();
  const restored = await page.getByLabel("想法输入框").inputValue();
  report("S6.4 草稿刷新恢复", restored.includes("hányè"), restored.slice(0, 24));

  // ---- S7 无伪入口：临时视图不出现「已交付阶段还进占位」 ----
  await page.getByRole("button", { name: /第一章 雨夜的客人/ }).first().click();
  const placeholder = page.getByText("在对应阶段接入真实数据，当前为占位视图。");
  // 已交付阶段却还点进占位视图的入口（正文候选/差异对比）必须不再展示
  for (const label of ["正文候选", "差异对比"]) {
    const count = await page.getByRole("button", { name: new RegExp(label) }).count();
    report(`S7 「${label}」伪入口已移除`, count === 0, count === 0 ? "不再展示" : "仍作为入口存在");
  }
  // 剩余入口必须都进真实视图
  for (const label of ["关联图谱", "版本历史"]) {
    const entry = page.getByRole("button", { name: new RegExp(label) }).first();
    await entry.click();
    await page.waitForTimeout(500);
    const fake = (await placeholder.count()) > 0;
    report(`S7 「${label}」非伪入口`, !fake, fake ? "点击后进入占位视图（伪入口）" : "进入真实视图");
    await page.getByRole("button", { name: /第一章 雨夜的客人/ }).first().click();
  }

  // ---- S8 文字可读：正文字号/行宽 + 对比度 ----
  await page.getByLabel("人工正文").waitFor({ state: "visible", timeout: 10000 });
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
    "S8.1 正文字号 ≥18px（M14 起点）",
    metrics.manuscriptFontPx >= 18,
    `实测 ${metrics.manuscriptFontPx}px / 行高 ${metrics.manuscriptLineHeight}px`,
  );
  const emWidth = metrics.manuscriptFontPx;
  report(
    "S8.2 正文行宽 ≤44em",
    metrics.manuscriptWidthPx <= 44 * emWidth + 1,
    `实测 ${metrics.manuscriptWidthPx}px ≈ ${(metrics.manuscriptWidthPx / emWidth).toFixed(1)}em`,
  );
  report(
    "S8.3 输入框字号 ≥14px",
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
    report(`S8.4 对比度 ${label} ≥4.5:1`, ratio >= 4.5, `实测 ${ratio.toFixed(2)}:1`);
  }
  await shot(page, "09-readability");

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

/** 等待状态 Pill 出现指定文字（保存状态在头部）。 */
async function expectPill(page, text) {
  await page
    .getByText(text, { exact: true })
    .first()
    .waitFor({ state: "visible", timeout: 10000 });
}
