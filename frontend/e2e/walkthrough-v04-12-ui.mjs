/**
 * V04-12 视觉走查：iOS 6 浅色化后的三栏、目录、对话栏与稿纸。
 *
 * 运行前置：
 *   1. frontend/ 已 npm run build；
 *   2. 服务已启动且没有预打开目录：
 *        uv run python -m hastings.server --port 8140 --state-dir ../data/test-tmp/v04-12-state
 * 用法：node e2e/walkthrough-v04-12-ui.mjs
 *
 * 脚本自己经真实接口准备一部作品（含一章正文与两份设定），不调用模型；
 * 只截界面，不做断言——断言在 workspace.spec.ts。
 * 截图归档：dev-notes/v0.4/artifacts/v04-12-ui/。
 */

import { chromium } from "playwright";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE = "http://127.0.0.1:8140";
const SHOTS = join(HERE, "..", "..", "dev-notes", "v0.4", "artifacts", "v04-12-ui");
const WORKSPACE = join(HERE, "..", "..", "data", "test-tmp", "v0412-walkthrough");

mkdirSync(SHOTS, { recursive: true });

async function api(path, init) {
  const response = await fetch(`${BASE}/api/file-workspaces${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) throw new Error(`${path} 失败：${response.status} ${await response.text()}`);
  return response.json();
}

rmSync(WORKSPACE, { recursive: true, force: true });
const binding = await api("", {
  method: "POST",
  body: JSON.stringify({ path: WORKSPACE, create: true }),
});

writeFileSync(
  join(WORKSPACE, "正文", "0001 雨夜.md"),
  [
    "# 雨夜",
    "",
    "侦探抵达旅馆时，雪已经下了三天。",
    "",
    "前台说只剩一间房。他没得选。",
    "",
    "走廊尽头有一扇没有号码的门。",
    "",
    "## 一",
    "",
    "他把手放在门把上，门是温的。",
    "",
    "旅馆里没有别人，只有壁炉还在烧。",
  ].join("\n"),
  "utf-8",
);
writeFileSync(
  join(WORKSPACE, "设定集", "线索.md"),
  "# 线索\n\n- 前台的手套是湿的\n- 走廊尽头有一扇没有号码的门\n",
  "utf-8",
);
await api(`/${binding.id}/git/record`, {
  method: "POST",
  body: JSON.stringify({ message: "写入第一章" }),
});

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto(BASE, { waitUntil: "networkidle" });
await page.getByLabel("选择作品目录").waitFor({ timeout: 10_000 });

const shot = async (name) => {
  await page.screenshot({ path: join(SHOTS, `${name}.png`) });
  console.log(`✓ ${name}`);
};

await page.getByRole("button", { name: "雨夜" }).click();
await page.getByText("侦探抵达旅馆时").waitFor();
await shot("01-reading");

// 收起对话栏：稿纸占满剩余宽度。
await page.getByRole("button", { name: "对话", exact: true }).click();
await shot("02-chat-hidden");
await page.getByRole("button", { name: "对话", exact: true }).click();

// 拖宽目录栏，看选中行高光与把手反馈。
const handle = page.getByRole("separator", { name: "目录栏宽度" });
const box = await handle.boundingBox();
await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
await page.mouse.down();
await page.mouse.move(box.x + box.width / 2 + 100, box.y + box.height / 2, { steps: 10 });
await page.mouse.up();
await page.getByRole("button", { name: "线索", exact: true }).click();
await page.getByText("前台的手套是湿的").waitFor();
await shot("03-wider-sidebar-settings");

// 抽屉与模型设置：确认这两个浮层也还是浅色，没有被样式清理带崩。
await page.getByRole("button", { name: "改动与历史" }).click();
await page.getByText("写入第一章").waitFor();
await shot("04-changes-drawer");
await page.getByRole("button", { name: "关闭" }).click();

await page.getByRole("button", { name: "模型设置" }).click();
await page.getByRole("dialog").waitFor();
await shot("05-model-settings");

await browser.close();
