/**
 * V05-2 对话体验走查：流式回复、折叠过程、Markdown、复制、展开与回到最新。
 *
 * 运行前置：
 *   1. frontend/ 已 npm run build；
 *   2. 真实后端已启动，且 config.yaml 里有可用连接（本脚本会真的调用模型）：
 *        uv run python -m hastings.server --port 8141 --state-dir data/test-tmp/v052-state
 * 用法：node e2e/walkthrough-v05-2-chat.mjs
 *
 * 与 spec 的分工：这里用真实供应商和真实浏览器走一遍读起来是什么样，同时留下截图与
 * 观察结果；确定性断言在 tests/workspace/test_chat_stream.py 与 src/state/*.test.ts。
 * 脚本自己准备作品，不改动作者自己的目录。
 *
 * 截图归档：dev-notes/v0.5/artifacts/v05-2-chat/。
 */

import { chromium } from "playwright";
import { mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", "..");
const BASE = process.env.HASTINGS_WALKTHROUGH_BASE ?? "http://127.0.0.1:8141";
const SHOTS = join(ROOT, "dev-notes", "v0.5", "artifacts", "v05-2-chat");
// 每次走查用全新作品目录：同一路径会复用上次的会话日志，看不出「从零开始」的行为。
const RUN = Date.now().toString(36);
const WORKSPACE = join(ROOT, "data", "test-tmp", `v052-walkthrough-${RUN}`);

/** 带正文的回复：排除只有思考的响应。 */
const REPLY = ".chat-reply:not(.chat-reply-plain)";

const observations = [];

function note(text) {
  observations.push(text);
  console.log(`· ${text}`);
}

async function api(path, init) {
  const response = await fetch(`${BASE}/api/file-workspaces${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) throw new Error(`${path} 失败：${response.status} ${await response.text()}`);
  return response.json();
}

mkdirSync(SHOTS, { recursive: true });
// 清掉旧一次走查的作品目录；当前这次重新创建。
for (const name of readdirSync(join(ROOT, "data", "test-tmp"))) {
  if (name.startsWith("v052-walkthrough")) {
    rmSync(join(ROOT, "data", "test-tmp", name), { recursive: true, force: true });
  }
}

const binding = await api("", {
  method: "POST",
  body: JSON.stringify({ path: WORKSPACE, create: true }),
});
writeFileSync(
  join(WORKSPACE, "正文", "0001 雨夜.md"),
  "# 雨夜\n\n侦探抵达旅馆时，雪已经下了三天。前台的灯一直亮着。\n",
  "utf-8",
);
await api(`/${binding.id}/git/record`, {
  method: "POST",
  body: JSON.stringify({ message: "写入第一章" }),
});

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  permissions: ["clipboard-read", "clipboard-write"],
});
const page = await context.newPage();
page.on("pageerror", (error) => console.error("页面错误：", error.message));

await page.goto(BASE);
await page.getByLabel("选择作品目录").waitFor({ timeout: 15_000 });
await page.locator(".chat-input").waitFor();

await page.locator(".chat-input").fill(
  "只讨论，不改文件：用一张三列表格列出这场雪夜开场可以铺开的三条线索（列名：线索、证据、风险），" +
    "再用两段话说明先写哪一条、为什么。",
);
await page.locator(".chat-input").press("Enter");

// -- 生成中：回复还没结束就已经能读 ---------------------------------------
const reply = page.locator(REPLY).first();
await reply.waitFor({ timeout: 120_000 });
const streamingText = await reply.innerText();
const finishedEarly = await page.locator(".chat-card.result, .chat-card.warn").count();
note(`首段可见时整轮结果卡数量：${finishedEarly}（0 表示模型尚未结束）`);
note(`首段可见长度：${streamingText.length} 字`);
await page.screenshot({ path: join(SHOTS, "01-streaming.png") });

// 状态行：等待模型 / 正在执行工具，二者都来自应用确认的阶段。
const status = page.locator(".chat-status");
if (await status.count()) note(`运行状态行：${(await status.first().innerText()).trim()}`);

// -- 结束：Markdown、结果卡 -------------------------------------------------
await page.locator(".chat-card").first().waitFor({ timeout: 180_000 });
await page.locator(".chat-status").first().waitFor({ state: "detached", timeout: 30_000 });
await page.screenshot({ path: join(SHOTS, "02-completed.png") });

const tables = await page.locator(".chat-table-wrap table").count();
const headings = await page.locator(".prose-chat h1, .prose-chat h2, .prose-chat h3, .prose-chat strong").count();
const thinking = await page.locator(".chat-thinking").count();
note(`结束回复中的表格：${tables} 个；标题/强调：${headings} 处`);
note(`思考块数量：${thinking}（0 表示本模型这次没有返回可展示的思考）`);

// 思考块默认折叠
if (thinking > 0) {
  const open = await page.locator(".chat-thinking").first().evaluate((node) => node.open);
  note(`思考块默认展开：${open}（应为 false）`);
}

// -- 复制：得到 Markdown 原文，不带思考与按钮文字 ---------------------------
const replyText = (await reply.innerText()).trim();
await page.getByRole("button", { name: "复制" }).first().click();
const copied = await page.evaluate(() => navigator.clipboard.readText());
note(`复制内容长度：${copied.length}；含「复制」按钮文字：${copied.includes("复制")}`);
note(`复制内容含表格管道符：${copied.includes("|")}`);
await page.screenshot({ path: join(SHOTS, "03-copied.png") });

// -- 展开阅读：同一条回复，关闭后回到原位置 ---------------------------------
const beforeExpand = await page.locator(".chat-scroll").evaluate((node) => node.scrollTop);
await page.getByRole("button", { name: "展开阅读" }).first().click();
await page.locator(".chat-dialog").waitFor({ timeout: 10_000 });
await page.screenshot({ path: join(SHOTS, "04-expanded.png") });
const dialogTables = await page.locator(".chat-dialog .chat-table-wrap table").count();
note(`宽阅读视图中的表格：${dialogTables} 个`);
await page.getByRole("button", { name: "关闭" }).click();
await page.locator(".chat-dialog").waitFor({ state: "detached", timeout: 10_000 });
const afterExpand = await page.locator(".chat-scroll").evaluate((node) => node.scrollTop);
note(`展开前后滚动位置：${beforeExpand} → ${afterExpand}`);

// -- 回到最新：向上阅读时不抢位置 -------------------------------------------
const scroll = page.locator(".chat-scroll");
await scroll.evaluate((node) => {
  node.scrollTop = 0;
  node.dispatchEvent(new Event("scroll"));
});
const jump = page.locator(".chat-jump");
if (await jump.count()) {
  await page.screenshot({ path: join(SHOTS, "05-return-to-latest.png") });
  note("向上滚动后出现「回到最新」");
  await jump.click();
  const atBottom = await scroll.evaluate(
    (node) => node.scrollHeight - node.scrollTop - node.clientHeight < 8,
  );
  note(`点击后回到底部：${atBottom}`);
} else {
  note("内容不足一屏，没有出现「回到最新」（视为正常）");
}

// -- 同一会话第二轮：上一轮保持原样，新回复接在后面 -------------------------
await page.locator(".chat-input").fill("只讨论：用一句话补充雨夜里的声音细节。");
await page.locator(".chat-input").press("Enter");
await page.waitForFunction(() => document.querySelectorAll(".chat-card").length >= 2, null, {
  timeout: 180_000,
});
await page.locator(".chat-status").first().waitFor({ state: "detached", timeout: 30_000 });
note(`两轮结束后结果卡：${await page.locator(".chat-card").count()} 个`);
note(`两轮结束后带正文的回复：${await page.locator(REPLY).count()} 条；其中只有思考的过程块：${await page.locator(".chat-reply-plain").count()} 个`);
await page.screenshot({ path: join(SHOTS, "07-second-turn.png") });

// -- 刷新后回放：与实时一致 --------------------------------------------------
await page.reload();
await page.getByLabel("选择作品目录").waitFor({ timeout: 15_000 });
await page.locator(REPLY).first().waitFor({ timeout: 30_000 });
const replayText = (await page.locator(REPLY).first().innerText()).trim();
note(`刷新后回复与实时一致：${replayText === replyText}`);
note(`刷新后表格数量：${await page.locator(".chat-table-wrap table").count()}`);
await page.screenshot({ path: join(SHOTS, "06-replay.png") });

writeFileSync(join(SHOTS, "observations.md"), `# V05-2 走查观察\n\n${observations.map((line) => `- ${line}`).join("\n")}\n`, "utf-8");
await browser.close();
console.log(`\n截图与观察：${SHOTS}`);