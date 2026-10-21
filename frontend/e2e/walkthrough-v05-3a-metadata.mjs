/**
 * V05-3A 走查：章序章名、命名诊断与人物身份标签。
 *
 * 夹具作品里刻意放了这些情况：一级标题是书名、缺第二章、序章与尾声、
 * 文件名编号与标题序号冲突、人物多标签与无标签。走查只读界面并截图，
 * 不改作品内容；真实模型不在这一步调用。
 */

import { chromium } from "playwright";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:8142";
const ROOT = "C:/Projects/Agent/myagent";
const SHOTS = join(ROOT, "dev-notes", "v0.5", "artifacts", "v05-3a-metadata");
const RUN = Date.now().toString(36);
// 作品名参与「一级标题是不是书名」的判断，目录名要与夹具叙事一致。
const WORKSPACE = join(ROOT, "data", "test-tmp", `v053a-walkthrough-${RUN}`, "雪山旅馆");
const observations = [];

function note(line) {
  observations.push(line);
  console.log(`- ${line}`);
}

rmSync(SHOTS, { recursive: true, force: true });
mkdirSync(SHOTS, { recursive: true });

const files = {
  "正文/0000 序章 暴雪前的信.md": "序章正文：信在雪里放了三天。\n",
  // 一级标题是书名：应回退文件名中的章名并给出命名提示。
  "正文/0001 暴雪来客.md": "# 雪山旅馆\n\n侦探抵达旅馆时，雪已经下了三天。\n",
  // 文件编号与标题序号冲突：显示沿用文件编号。
  "正文/0003 书房里的脚印.md": "# 第五章 书房里的脚印\n\n地毯上有一串湿脚印。\n",
  "正文/0012 尾声.md": "尾声正文：雪停了。\n",
  "设定集/角色小传/林远.md": "---\nroles: [detective]\n---\n# 林远\n\n身份：私家侦探。\n",
  "设定集/角色小传/许衡.md": "---\nroles: [suspect, culprit]\n---\n# 许衡\n\n身份：旅馆客人。\n",
  "设定集/角色小传/陈默.md": "# 陈默\n\n身份：旅馆前台。\n",
};

for (const [path, text] of Object.entries(files)) {
  const target = join(WORKSPACE, path);
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, text, "utf-8");
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto(BASE);
await page.getByLabel("选择作品目录").waitFor({ timeout: 15_000 });

// 打开夹具作品：界面通过真实接口列出目录。
await page.evaluate(async (path) => {
  const response = await fetch("/api/file-workspaces", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, create: false }),
  });
  return response.json();
}, WORKSPACE);
await page.reload();
await page.getByLabel("选择作品目录").waitFor({ timeout: 15_000 });
await page.waitForTimeout(800);

const toc = page.locator("main > nav");
const tocText = (await toc.innerText()).replace(/\s+/g, " ");
note(`目录显示：${tocText.slice(0, 220)}`);
await page.screenshot({ path: join(SHOTS, "01-toc.png") });

await page.getByRole("button", { name: "书房里的脚印" }).click();
await page.waitForTimeout(400);
const paper = page.locator("article");
note(`冲突章节的稿纸标题：${(await paper.locator("h2").first().innerText()).trim()}`);
note(`冲突章节的命名提示：${(await paper.locator(".meta-warning").innerText()).trim()}`);
await page.screenshot({ path: join(SHOTS, "02-chapter.png") });

await page.getByRole("button").filter({ hasText: "许衡" }).click();
await page.waitForTimeout(400);
const badges = await paper.locator(".role-badge").allInnerTexts();
note(`许衡的身份标签：${badges.join("、")}`);
note(`稿纸是否出现元信息：${(await paper.innerText()).includes("roles:")}`);
await page.screenshot({ path: join(SHOTS, "03-character.png") });

writeFileSync(
  join(SHOTS, "observations.md"),
  `# V05-3A 走查观察\n\n${observations.map((line) => `- ${line}`).join("\n")}\n`,
  "utf-8",
);
await browser.close();
console.log(`\n截图与观察：${SHOTS}`);
