/**
 * V04-4 浏览器走查：文件工作区（目录 → 章节 → 稿纸 → 对话 → 改动与历史 → 撤销）。
 *
 * 运行前置：
 *   1. frontend/ 已 npm run build；
 *   2. 服务已启动，且**没有**预打开目录：
 *        HASTINGS_STATE_DIR=<空状态目录> uv run python -m hastings.server --port 8140
 * 用法：node e2e/walkthrough-v04-workspace.mjs
 *
 * 脚本自己通过真实接口准备作品：新建目录（含 Git 基线）→ 写入一章 → 记一次提交 →
 * 写设定 → 再记一次提交；然后只用界面完成阅读、搜索、查看历史、撤销、外部刷新与新建作品。
 * 不调用模型：创作路径由后端假 provider 测试覆盖，真实模型另行取证。
 *
 * 证据归档：dev-notes/v0.4/artifacts/walkthrough-v04-4/。
 */

import { chromium } from "playwright";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE = "http://127.0.0.1:8140";
const SHOTS = join(HERE, "..", "..", "dev-notes", "v0.4", "artifacts", "walkthrough-v04-4");
const WORKSPACE = join(HERE, "..", "..", "data", "test-tmp", "walkthrough-作品");
const SECOND = join(HERE, "..", "..", "data", "test-tmp", "walkthrough-new");

mkdirSync(SHOTS, { recursive: true });

async function api(path, init) {
  const response = await fetch(`${BASE}/api/file-workspaces${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) throw new Error(`${path} 失败：${response.status} ${await response.text()}`);
  return response.json();
}

async function prepareWorkspace() {
  rmSync(WORKSPACE, { recursive: true, force: true });
  rmSync(SECOND, { recursive: true, force: true });
  const binding = await api("", {
    method: "POST",
    body: JSON.stringify({ path: WORKSPACE, create: true }),
  });
  writeFileSync(
    join(WORKSPACE, "正文", "0001 雨夜.md"),
    "# 雨夜\n\n侦探抵达旅馆时，雪已经下了三天。\n\n前台说只剩一间房。\n",
    "utf-8",
  );
  await api(`/${binding.id}/git/record`, {
    method: "POST",
    body: JSON.stringify({ message: "写入第一章" }),
  });
  const clue = await api(`/${binding.id}/file?path=${encodeURIComponent("设定集/线索.md")}`);
  writeFileSync(
    join(WORKSPACE, "设定集", "线索.md"),
    "# 线索\n\n- 前台的手套是湿的\n- 走廊尽头有一扇没有号码的门\n",
    "utf-8",
  );
  await api(`/${binding.id}/git/record`, {
    method: "POST",
    body: JSON.stringify({ message: "补充线索" }),
  });
  void clue;
  return binding;
}

const binding = await prepareWorkspace();
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const problems = [];
page.on("console", (message) => {
  if (message.type() === "error") problems.push(`console: ${message.text()}`);
});
page.on("pageerror", (error) => problems.push(`pageerror: ${error.message}`));

await page.goto(BASE, { waitUntil: "networkidle" });

const steps = [];
const step = async (name, action) => {
  try {
    await action();
    steps.push(`✓ ${name}`);
    console.log(`✓ ${name}`);
  } catch (error) {
    steps.push(`✗ ${name}: ${error.message}`);
    console.log(`✗ ${name}: ${error.message}`);
    await page.screenshot({ path: join(SHOTS, `fail-${steps.length}.png`) });
    throw error;
  }
};

await step("顶栏显示已打开的目录", async () => {
  await page.getByLabel("选择作品目录").waitFor({ timeout: 8000 });
  const value = await page.getByLabel("选择作品目录").inputValue();
  if (value !== binding.id) throw new Error(`选中的不是预期目录：${value}`);
});

await step("左栏列出章节、角色分组与固定设定", async () => {
  await page.getByRole("button", { name: "雨夜" }).waitFor();
  await page.locator("summary").filter({ hasText: "角色小传" }).waitFor();
  for (const title of ["线索", "真相"]) {
    await page.getByRole("button", { name: title, exact: true }).waitFor();
  }
});

await step("点击章节显示正文", async () => {
  await page.getByRole("button", { name: "雨夜" }).click();
  await page.getByText("侦探抵达旅馆时").waitFor();
});

await step("切换设定显示线索内容", async () => {
  await page.getByRole("button", { name: "线索", exact: true }).click();
  await page.getByText("前台的手套是湿的").waitFor();
  await page.screenshot({ path: join(SHOTS, "01-reading.png") });
});

await step("改动与历史显示两次提交", async () => {
  await page.getByRole("button", { name: "改动与历史" }).click();
  await page.getByText("补充线索").waitFor();
  await page.getByText("写入第一章").waitFor();
  await page.screenshot({ path: join(SHOTS, "02-history.png") });
});

await step("撤销最新一次提交后内容回退", async () => {
  await page.getByRole("button", { name: "撤销" }).first().click();
  await page.getByText(/Revert/).waitFor({ timeout: 10000 });
  await page.screenshot({ path: join(SHOTS, "03-reverted.png") });
  await page.getByRole("button", { name: "关闭" }).click();
  await page.getByRole("button", { name: "线索", exact: true }).click();
  await page.getByText("这一页还是空的").waitFor();
});

await step("搜索能定位到正文内容", async () => {
  await page.getByRole("button", { name: "雨夜" }).click();
  await page.getByLabel("搜索正文与设定").fill("旅馆");
  await page.getByRole("button", { name: "查找" }).click();
  await page.getByText(/搜索结果/).waitFor();
  await page.getByText(/正文\/0001 雨夜\.md/).first().waitFor();
  await page.screenshot({ path: join(SHOTS, "04-search.png") });
  await page.getByRole("button", { name: "返回目录" }).click();
});

await step("外部新增的章节刷新后可见", async () => {
  writeFileSync(
    join(WORKSPACE, "正文", "0002 来信.md"),
    "# 来信\n\n早上，信封从门缝里塞进来。\n",
    "utf-8",
  );
  await page.getByRole("button", { name: "重新读取文件" }).click();
  await page.getByRole("button", { name: "来信" }).waitFor();
});

await step("通过对话框新建作品并建立版本记录", async () => {
  await page.getByRole("button", { name: "打开 / 新建" }).click();
  await page.getByLabel("目录路径").fill(SECOND);
  await page.getByLabel(/新建作品/).check();
  await page.getByRole("button", { name: "新建并打开" }).click();
  // 左栏在切换完成前仍是上一部作品的条目，等底部路径标签变成新作品再断言。
  await page.waitForFunction(
    (path) => document.body.innerText.includes(path),
    SECOND,
    { timeout: 10000 },
  );
  await page.locator("summary").filter({ hasText: "角色小传" }).waitFor();
  await page.getByRole("button", { name: "改动与历史" }).click();
  await page.getByText("初始化作品目录").waitFor();
  await page.getByRole("button", { name: "关闭" }).click();
});

await step("对话栏提示可以直接提要求", async () => {
  await page.getByText(/直接说要求/).waitFor();
  await page.screenshot({ path: join(SHOTS, "05-new-workspace.png") });
});

console.log("console problems:", problems.length ? problems : "none");
writeFileSync(join(SHOTS, "results.txt"), `${steps.join("\n")}\n`, "utf-8");
await browser.close();
