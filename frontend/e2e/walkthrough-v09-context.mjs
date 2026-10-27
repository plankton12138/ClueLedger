/**
 * V09 浏览器走查：上下文入口、累计入口、整理后的显示、窄栏与键盘。
 *
 * 运行前置：
 *   1. frontend/ 已 npm run build；
 *   2. 服务已启动：uv run python -m hastings.server --port 8140 --state-dir <状态目录>
 * 用法：node e2e/walkthrough-v09-context.mjs <同一个状态目录>
 *
 * 会话事件直接写入状态目录，界面之外不调用模型：本脚本验证入口、浮层、
 * 状态文案、窄栏与键盘；运行中整理与停止属于在线模型路径，不在本脚本内模拟。
 * 证据归档：dev-notes/v0.9/artifacts/walkthrough-context/。
 */

import { chromium } from "playwright";
import { mkdirSync, rmSync, writeFileSync, appendFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE = process.env.HASTINGS_WALKTHROUGH_BASE ?? "http://127.0.0.1:8140";
const SHOTS = join(HERE, "..", "..", "dev-notes", "v0.9", "artifacts", "walkthrough-context");
const WORKSPACE = join(HERE, "..", "..", "data", "test-tmp", "walkthrough-v09-book");
const STATE_DIR = process.argv[2] ?? process.env.HASTINGS_STATE_DIR;
const AT = "2026-10-27T15:00:00+08:00";

mkdirSync(SHOTS, { recursive: true });

async function api(path, init) {
  const response = await fetch(`${BASE}/api/file-workspaces${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) throw new Error(`${path} 失败：${response.status} ${await response.text()}`);
  return response.json();
}

function estimate(total, window) {
  return {
    model: "deepseek:deepseek-chat",
    total_tokens: total,
    system_tokens: 1000,
    tool_tokens: 500,
    message_tokens: total - 1600,
    protocol_tokens: 100,
    method: "cjk-aware-v1",
    fingerprint: `walk-${total}`,
    budget: window === null ? "unknown" : total > window * 0.8 ? "near" : "ok",
    context_window_tokens: window,
    input_limit_tokens: window === null ? null : window - 4096 - 3200,
    output_reserve_tokens: 4096,
    limits_revision: "walk-r1",
  };
}

function record(cid, seq, event) {
  return {
    seq,
    at: AT,
    event_version: 1,
    event_id: `${cid}:${seq}`,
    conversation_id: cid,
    ...event,
  };
}

function request(cid, seq, operation, stage, total = null, window = null) {
  return record(cid, seq, {
    type: "model_request_started",
    turn_id: "t1",
    operation_id: operation,
    stage,
    ...(total === null ? {} : { request_estimate: estimate(total, window) }),
  });
}

function finished(cid, seq, operation, stage, usage) {
  return record(cid, seq, {
    type: "model_request_finished",
    turn_id: "t1",
    operation_id: operation,
    stage,
    status: "completed",
    usage,
    duration_ms: 900,
  });
}

function writeConversation(root, cid, events) {
  writeFileSync(
    join(root, `${cid}.jsonl`),
    events.map((event) => JSON.stringify(event)).join("\n") + "\n",
    "utf-8",
  );
}

async function prepare() {
  rmSync(WORKSPACE, { recursive: true, force: true });
  const binding = await api("", {
    method: "POST",
    body: JSON.stringify({ path: WORKSPACE, create: true }),
  });
  writeFileSync(join(WORKSPACE, "正文", "0001 雨夜.md"), "# 雨夜\n\n侦探抵达旅馆。\n", "utf-8");
  await api(`/${binding.id}/git/record`, {
    method: "POST",
    body: JSON.stringify({ message: "走查初始稿" }),
  });
  if (!STATE_DIR) throw new Error("需要把服务使用的状态目录作为参数传入");
  const root = join(STATE_DIR, "workspaces", binding.id);
  mkdirSync(root, { recursive: true });

  // 1) 接近上限，之后追加整理：先看到 81%，再看到 28%。
  writeConversation(root, "c_walk_1_near", [
    record("c_walk_1_near", 1, { type: "turn_started", turn_id: "t1", text: "写旅馆走廊" }),
    record("c_walk_1_near", 2, {
      type: "assistant_message",
      turn_id: "t1",
      text: "第一稿把走廊写长了，灯管和水管都交代了。",
    }),
    request("c_walk_1_near", 3, "op1", "write", 52000, 64000),
    finished("c_walk_1_near", 4, "op1", "write", {
      input: 54000,
      output: 800,
      total_tokens: 54800,
    }),
  ]);
  // 2) 窗口未知；用量也没有返回。
  writeConversation(root, "c_walk_2_unknown", [
    record("c_walk_2_unknown", 1, { type: "turn_started", turn_id: "t1", text: "讨论雨声" }),
    request("c_walk_2_unknown", 2, "op1", "write", 9000, null),
    finished("c_walk_2_unknown", 3, "op1", "write", null),
  ]);
  // 3) 两次请求，其中一次没有用量。
  writeConversation(root, "c_walk_3_partial", [
    record("c_walk_3_partial", 1, { type: "turn_started", turn_id: "t1", text: "补一段灯影" }),
    request("c_walk_3_partial", 2, "op1", "write", 30000, 64000),
    finished("c_walk_3_partial", 3, "op1", "write", {
      input: 31000,
      output: 700,
      total_tokens: 31700,
    }),
    request("c_walk_3_partial", 4, "op2", "write", 32000, 64000),
    finished("c_walk_3_partial", 5, "op2", "write", null),
  ]);
  // 4) 还没有发出请求。
  writeConversation(root, "c_walk_4_pending", [
    record("c_walk_4_pending", 1, { type: "turn_started", turn_id: "t1", text: "新开一段" }),
  ]);

  return { workspaceId: binding.id, root };
}

function appendCompaction(root, cid) {
  const lines = [
    record(cid, 5, {
      type: "context_compacted",
      turn_id: "t1",
      compaction_id: "cmp_walk_1",
      previous_id: null,
      summary: "走廊第一稿与端茶约束",
      source_start: 1,
      source_end: 4,
    }),
    request(cid, 6, "op2", "compact"),
    finished(cid, 7, "op2", "compact", { input: 6000, output: 300, total_tokens: 6300 }),
    request(cid, 8, "op3", "write", 18000, 64000),
    finished(cid, 9, "op3", "write", { input: 18200, output: 900, total_tokens: 19100 }),
    record(cid, 10, {
      type: "run_finished",
      turn_id: "t1",
      status: "completed",
      changed_paths: [],
      recording: "no_change",
    }),
  ];
  appendFileSync(
    join(root, `${cid}.jsonl`),
    lines.map((event) => JSON.stringify(event)).join("\n") + "\n",
    "utf-8",
  );
}

function rectsOverlap(a, b) {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

async function main() {
  const { workspaceId, root } = await prepare();
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const problems = [];
  page.on("pageerror", (error) => problems.push(`页面错误：${error}`));

  const open = async (width = 1440, height = 900) => {
    await page.setViewportSize({ width, height });
    await page.goto(`${BASE}/#/workspaces/${workspaceId}/writing`, { waitUntil: "networkidle" });
    await page.waitForSelector("select[aria-label='选择对话']");
  };
  const select = async (cid) => {
    await page.selectOption("select[aria-label='选择对话']", cid);
    await page.waitForSelector("button[aria-label='当前上下文占用']");
    await page.waitForTimeout(300);
  };
  const entry = () => page.locator("button[aria-label='当前上下文占用']");
  const usageEntry = () => page.locator("button[aria-label='本次对话累计用量']");

  await open();
  await select("c_walk_1_near");

  // UI01：两个入口默认低强调、互斥、Escape 与外部点击关闭。
  if ((await page.locator(".chat-usage-entry").count()) !== 2) problems.push("入口数量不是两个");
  await entry().click();
  let contextOpen = await page.locator("[role='dialog'][aria-label='当前上下文']").isVisible();
  if (!contextOpen) problems.push("点击上下文入口没有打开浮层");
  await usageEntry().click();
  if (await page.locator("[role='dialog'][aria-label='当前上下文']").isVisible()) {
    problems.push("打开累计浮层时上下文浮层没有关闭");
  }
  if (!(await page.locator("[role='dialog'][aria-label='本次对话累计用量']").isVisible())) {
    problems.push("点击累计入口没有打开浮层");
  }
  await page.screenshot({ path: join(SHOTS, "01-usage-pop-1440.png"), fullPage: true });
  await page.keyboard.press("Escape");
  if (await page.locator("[role='dialog']").isVisible().catch(() => false)) {
    problems.push("Escape 没有关闭浮层");
  }
  await entry().click();
  await page.keyboard.press("Escape");
  if (await page.locator("[role='dialog']").isVisible().catch(() => false)) {
    problems.push("Escape 没有关闭上下文浮层");
  }
  await entry().click();
  await page.locator(".chat-input").click();
  if (await page.locator("[role='dialog']").isVisible().catch(() => false)) {
    problems.push("点击外部没有关闭浮层");
  }

  // UI06 前：整理前显示 81% 与一次请求。
  const beforeContext = await entry().innerText();
  if (!beforeContext.includes("81%")) problems.push(`整理前上下文不是 81%：${beforeContext}`);
  await entry().click();
  const beforeDialog = await page.locator("[role='dialog'][aria-label='当前上下文']").innerText();
  for (const label of ["约定与资料", "工具定义", "对话与结果", "回复预留"]) {
    if (!beforeDialog.includes(label)) problems.push(`上下文浮层缺少「${label}」`);
  }
  await usageEntry().click();
  const beforeUsage = await page.locator("[role='dialog'][aria-label='本次对话累计用量']").innerText();
  if (!beforeUsage.includes("1 次请求") || !beforeUsage.includes("5.5 万")) {
    problems.push(`整理前累计显示不符：${beforeUsage.replaceAll("\n", " / ")}`);
  }
  await page.keyboard.press("Escape");

  // 追加整理与摘要调用，重新加载：上下文下降，累计包含摘要请求。
  appendCompaction(root, "c_walk_1_near");
  await open();
  await select("c_walk_1_near");
  const afterContext = await entry().innerText();
  if (!afterContext.includes("28%")) problems.push(`整理后上下文不是 28%：${afterContext}`);
  await usageEntry().click();
  const afterUsage = await page.locator("[role='dialog'][aria-label='本次对话累计用量']").innerText();
  if (!afterUsage.includes("3 次请求")) problems.push(`整理后请求数不对：${afterUsage}`);
  if (afterUsage.includes("部分未统计")) problems.push("完整用量被标成部分未统计");
  await page.screenshot({ path: join(SHOTS, "02-after-compaction.png"), fullPage: true });
  await page.keyboard.press("Escape");

  // UI02：未知、部分、待计算三种状态。
  await select("c_walk_2_unknown");
  if (!(await entry().innerText()).includes("≈9000")) problems.push("窗口未知时没有显示估算值");
  if (!(await usageEntry().innerText()).includes("未知")) problems.push("没有用量的会话没有标成未知");
  await usageEntry().click();
  if (!(await page.locator("[role='dialog']").innerText()).includes("容量未知")) {
    problems.push("窗口未知时浮层没有说明容量未知");
  }
  await page.keyboard.press("Escape");

  await select("c_walk_3_partial");
  if (!(await usageEntry().innerText()).includes("部分未统计")) {
    problems.push("缺一条用量的会话没有标成部分未统计");
  }

  await select("c_walk_4_pending");
  if (!(await entry().innerText()).includes("待计算")) problems.push("新会话没有显示待计算");
  if (!(await usageEntry().innerText()).includes("累计 0")) problems.push("新会话没有显示累计 0");

  // UI04 的草稿保留（不覆盖延迟请求，只验证切换不丢草稿）。
  await select("c_walk_1_near");
  const draft = "走查草稿：切走再回来不能丢。";
  await page.fill(".chat-input", draft);
  await select("c_walk_2_unknown");
  await select("c_walk_1_near");
  if ((await page.inputValue(".chat-input")) !== draft) problems.push("切换会话后草稿没有保留");

  // UI03：窄栏与缩放；浮层不越界、不遮住发送按钮。
  for (const [width, height, shot] of [
    [1440, 900, "03-1440.png"],
    [1024, 768, "04-1024.png"],
    [640, 900, "05-640.png"],
    [320, 720, "06-320.png"],
    [960, 600, "07-150pct.png"],
  ]) {
    await open(width, height);
    await select("c_walk_1_near");
    await entry().click();
    await page.screenshot({ path: join(SHOTS, shot), fullPage: true });
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    );
    if (overflow) problems.push(`${shot} 出现整页横向滚动`);
    const pop = await page.locator("[role='dialog'][aria-label='当前上下文']").boundingBox();
    const send = await page.getByRole("button", { name: /发送|停止|正在停止/ }).boundingBox();
    if (!pop) problems.push(`${shot} 浮层没有可见边界`);
    else if (pop.x < 0 || pop.x + pop.width > width + 0.5) {
      problems.push(`${shot} 浮层越出视口：x=${pop.x}, w=${pop.width}`);
    }
    if (pop && send && rectsOverlap(pop, send)) problems.push(`${shot} 浮层遮住发送按钮`);
    await page.keyboard.press("Escape");
  }

  // UI07：键盘打开、Escape 关闭、焦点回到入口。
  await open();
  await select("c_walk_1_near");
  await page.focus("button[aria-label='当前上下文占用']");
  await page.keyboard.press("Enter");
  if (!(await page.locator("[role='dialog'][aria-label='当前上下文']").isVisible())) {
    problems.push("键盘 Enter 没有打开上下文浮层");
  }
  await page.keyboard.press("Escape");
  const active = await page.evaluate(() => document.activeElement?.getAttribute("aria-label"));
  if (active !== "当前上下文占用") problems.push(`Escape 后焦点没有回到入口：${active}`);

  await browser.close();
  if (!process.env.KEEP_WALKTHROUGH) {
    rmSync(WORKSPACE, { recursive: true, force: true });
    await api("/stale", { method: "DELETE" }).catch(() => undefined);
  }
  if (problems.length) {
    console.error("走查发现问题：\n- " + problems.join("\n- "));
    process.exitCode = 1;
  } else {
    console.log("走查通过，截图：", SHOTS);
  }
}

await main();
