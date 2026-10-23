/**
 * V07 浏览器走查：故事资料页（导航、截止章、五页、证据、草稿保留、窄窗口）。
 *
 * 运行前置：
 *   1. frontend/ 已 npm run build；
 *   2. 服务已启动，且**没有**预打开目录：
 *        uv run python -m hastings.server --port 8140 --state-dir <空状态目录>
 * 用法：node e2e/walkthrough-v07-materials.mjs <同一个状态目录>
 *
 * 脚本通过真实接口准备作品与派生记录（时间线、章节概览、整理覆盖），再只用界面走查。
 * 它不调用模型：整理任务本身由后端受控测试覆盖，这里验证的是页面、导航与证据展示。
 * 证据归档：dev-notes/v0.7/artifacts/walkthrough-v07/。
 */

import { chromium } from "playwright";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE = "http://127.0.0.1:8140";
const SHOTS = join(HERE, "..", "..", "dev-notes", "v0.7", "artifacts", "walkthrough-v07");
const WORKSPACE = join(HERE, "..", "..", "data", "test-tmp", "walkthrough-v07-book");
const STATE_DIR = process.argv[2] ?? process.env.HASTINGS_STATE_DIR;

mkdirSync(SHOTS, { recursive: true });

async function api(path, init) {
  const response = await fetch(`${BASE}/api/file-workspaces${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) throw new Error(`${path} 失败：${response.status} ${await response.text()}`);
  return response.json();
}

function evidenceOf(path, digest, chapter, text, needle) {
  const start = text.indexOf(needle);
  if (start < 0) throw new Error(`引文不在原文里：${needle}`);
  return { path, digest, chapter, start, end: start + needle.length, quote: needle };
}

async function prepare() {
  rmSync(WORKSPACE, { recursive: true, force: true });
  const binding = await api("", {
    method: "POST",
    body: JSON.stringify({ path: WORKSPACE, create: true }),
  });
  const chapters = [
    ["0001 雨夜.md", "# 雨夜\n\n10月21日深夜，侦探抵达旅馆。前台的手套是湿的。\n"],
    ["0002 清晨.md", "# 清晨\n\n次日清晨，走廊尽头那扇没有号码的门开着。\n"],
    ["0003 午后.md", "# 午后\n\n午后，没有人再提起那扇门。\n"],
  ];
  for (const [name, text] of chapters) {
    writeFileSync(join(WORKSPACE, "正文", name), text, "utf-8");
  }
  writeFileSync(
    join(WORKSPACE, "设定集", "角色小传", "林静.md"),
    "---\nroles: [detective]\nsurface: 私家侦探\nrevealed: [1:受雇调查失踪案]\nplanned: [3:隐瞒了委托人的身份]\n---\n# 林静\n\n四十岁，话少。\n",
    "utf-8",
  );
  await api(`/${binding.id}/git/record`, {
    method: "POST",
    body: JSON.stringify({ message: "写入前三章" }),
  });

  const files = {};
  for (const [name, text] of chapters) {
    const view = await api(`/${binding.id}/file?path=${encodeURIComponent(`正文/${name}`)}`);
    files[name] = { path: view.path, digest: view.digest, text, chapter: Number(name.slice(0, 4)) };
  }

  if (!STATE_DIR) {
    throw new Error("需要把服务使用的状态目录作为参数传入（与服务 --state-dir 一致）");
  }
  const root = join(STATE_DIR, "workspaces", binding.id);
  mkdirSync(root, { recursive: true });
  const now = new Date().toISOString();

  const first = files["0001 雨夜.md"];
  const second = files["0002 清晨.md"];
  const third = files["0003 午后.md"];
  const timeline = {
    record_version: 1,
    recipe_version: 2,
    generated_at: now,
    entries: [
      {
        chapter: 1,
        when: "10月21日深夜",
        span: "一夜",
        location: "旅馆前台",
        evidence: evidenceOf(first.path, first.digest, 1, first.text, "10月21日深夜"),
      },
      {
        chapter: 2,
        when: "次日清晨",
        span: "",
        location: "走廊",
        evidence: evidenceOf(second.path, second.digest, 2, second.text, "次日清晨"),
      },
    ],
  };
  writeFileSync(join(root, "timeline.json"), JSON.stringify(timeline, null, 2), "utf-8");

  const index = {
    version: 1,
    recipe_version: 1,
    entries: {
      [first.path]: {
        source_path: first.path,
        source_digest: first.digest,
        recipe_version: 1,
        generated_at: now,
        summary: "侦探在雨夜抵达旅馆，发现前台的手套是湿的。",
        evidence: [
          {
            index: 1,
            start: first.text.indexOf("前台的手套是湿的"),
            end: first.text.indexOf("前台的手套是湿的") + "前台的手套是湿的".length,
            quote: "前台的手套是湿的",
          },
        ],
        events: [{ kind: "event", text: "侦探抵达旅馆", evidence: [1] }],
        characters: [{ kind: "character", text: "林静", evidence: [1] }],
        places: [{ kind: "place", text: "旅馆前台", evidence: [1] }],
        clues: [{ kind: "clue", text: "前台的手套是湿的", evidence: [1] }],
      },
    },
  };
  writeFileSync(join(root, "chapter-index.json"), JSON.stringify(index, null, 2), "utf-8");

  const coverage = {
    record_version: 1,
    recipe_version: 1,
    generated_at: now,
    records: [
      {
        domain: "timeline",
        path: first.path,
        entity_path: "",
        digest: first.digest,
        outcome: "found",
        task_id: "t_walkthrough",
        at: now,
        note: "",
      },
      {
        domain: "timeline",
        path: third.path,
        entity_path: "",
        digest: third.digest,
        outcome: "empty",
        task_id: "t_walkthrough",
        at: now,
        note: "这一章没有写时间",
      },
    ],
  };
  writeFileSync(join(root, "materials-coverage.json"), JSON.stringify(coverage, null, 2), "utf-8");

  return binding.id;
}

async function main() {
  const workspaceId = await prepare();
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const problems = [];
  page.on("pageerror", (error) => problems.push(String(error)));

  await page.goto(`${BASE}/#/workspaces/${workspaceId}/writing`, { waitUntil: "networkidle" });
  await page.waitForSelector(".topbar");

  // 草稿隔离：先在写作页写下非空草稿，再进出资料页。
  const draft = "草稿保留测试：不要丢掉这段字。";
  await page.fill(".chat-input", draft);
  await page.getByRole("button", { name: "故事资料", exact: true }).click();
  await page.waitForSelector(".materials-nav");
  await page.screenshot({ path: join(SHOTS, "01-chapters.png"), fullPage: true });

  for (const [label, file] of [
    ["时间线", "02-timeline.png"],
    ["人物", "03-characters.png"],
    ["线索与伏笔", "04-clues.png"],
    ["名词表", "05-glossary.png"],
  ]) {
    await page.getByRole("button", { name: label, exact: true }).click();
    await page.waitForTimeout(250);
    await page.screenshot({ path: join(SHOTS, file), fullPage: true });
  }

  // 时间线的证据栏与「查看截至」
  await page.getByRole("button", { name: "时间线", exact: true }).click();
  await page.waitForTimeout(200);
  await page.getByText("10月21日深夜", { exact: false }).first().click();
  await page.waitForTimeout(200);
  await page.screenshot({ path: join(SHOTS, "06-evidence.png"), fullPage: true });
  const evidenceVisible = await page.getByText("原文依据").isVisible();
  if (!evidenceVisible) problems.push("证据栏没有显示");

  await page.selectOption('select[aria-label="查看截至第几章"]', "2");
  await page.waitForTimeout(300);
  await page.screenshot({ path: join(SHOTS, "07-as-of-chapter.png"), fullPage: true });

  // 返回写作：草稿必须还在。
  await page.getByRole("button", { name: "写作", exact: true }).click();
  await page.waitForSelector(".chat-input");
  const restored = await page.inputValue(".chat-input");
  if (restored !== draft) problems.push(`草稿没有保留：${restored}`);

  // 不开放独立检查报告页面，也不留旧抽屉入口。
  const bodyText = await page.locator("body").innerText();
  for (const forbidden of ["检查报告", "章节索引", "状态面板"]) {
    if (bodyText.includes(forbidden)) problems.push(`出现了未开放的入口：${forbidden}`);
  }

  // 窄窗口与 150% 字号：不出现全页横向滚动。
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto(`${BASE}/#/workspaces/${workspaceId}/materials/timeline?chapter=2`, {
    waitUntil: "networkidle",
  });
  await page.waitForSelector(".materials-nav");
  await page.screenshot({ path: join(SHOTS, "08-1024.png"), fullPage: true });
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  );
  if (overflow) problems.push("1024 宽度出现横向滚动");

  await page.setViewportSize({ width: 760, height: 900 });
  await page.screenshot({ path: join(SHOTS, "09-narrow.png"), fullPage: true });

  await browser.close();
  // 清理临时作品与登记：走真实接口，不手改状态文件。调试时设 KEEP_WALKTHROUGH=1 保留现场。
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
