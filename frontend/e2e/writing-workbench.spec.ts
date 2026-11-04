import { test, expect, type Page } from "@playwright/test";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { resolve, join } from "node:path";

import { seedChapter } from "./fixtures/chapter";

const screenshotDir = resolve(
  process.env.HASTINGS_E2E_SCREENSHOT_DIR ?? "../dev-notes/v1.2/artifacts/writing",
);

async function shot(page: Page, name: string) {
  mkdirSync(screenshotDir, { recursive: true });
  await page.screenshot({ path: join(screenshotDir, `${name}.png`) });
}

test("空作品只有对话可创作，章节正文以只读稿纸呈现", async ({ page, request }) => {
  const parent = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "books");
  mkdirSync(parent, { recursive: true });
  const root = join(mkdtempSync(join(parent, "writing-workbench-")), "作品");
  const opened = await request.post("/api/file-workspaces", {
    data: { path: root, create: true },
  });
  expect(opened.ok(), await opened.text()).toBeTruthy();
  const book = await opened.json();
  const base = `/api/file-workspaces/${book.id}`;

  await page.goto(`/#/workspaces/${book.id}/writing`);
  const chat = page.locator(".chat-input");
  await expect(chat).toBeVisible();
  await expect(chat).toBeFocused();
  await expect(page.getByRole("textbox", { name: "正文编辑" })).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "作品意图" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /新建章节|导入正文|登记为章节|添加模块/ })).toHaveCount(0);
  const structure = page.getByRole("region", { name: "全书结构", exact: true });
  await expect(structure.getByText("尚未建立")).toBeVisible();
  await expect(structure.getByText("写正文前，要先有这本书的结构。")).toBeVisible();
  await expect(structure.getByRole("button", { name: "在对话里建立结构" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "先定结构，再写正文" })).toBeVisible();
  await expect(page.getByText(
    "全书结构是模型写正文前读取的全局依据。说一句你想写什么，模型会先整理结构，再开始第一章。",
    { exact: true },
  )).toBeVisible();
  const starter = page.getByRole("button", { name: "“我还没想好，带我一步步想。”" });
  await starter.click();
  await expect(chat).toHaveValue("我还没想好，带我一步步想。");
  await expect(chat).toBeFocused();
  await page.getByRole("button", { name: "“我想写一个暴雪封山旅馆里的失踪案。”" }).click();
  await expect(chat).toHaveValue("我还没想好，带我一步步想。");
  const conversations = await request.get(`${base}/conversations`).then(response => response.json());
  expect(conversations.items).toEqual([]);
  await shot(page, "01-empty-paper-and-chat");

  // 测试夹具直接走内部建章事务；写作页不暴露创建或编辑入口。
  const chapter = seedChapter(book, "雨夜", "e2e-create-chapter");
  const saved = await request.put(`${base}/story/chapters/${chapter.chapter_id}/prose`, {
    data: {
      text: "# 雨夜\n\n侦探抵达旅馆。海面上没有灯。\n",
      expected_digest: chapter.prose_digest,
      draft_revision: "e2e-seed",
      request_key: "e2e-save-prose",
    },
  });
  expect(saved.ok(), await saved.text()).toBeTruthy();

  await page.reload();
  await expect(structure.getByText("已就绪")).toBeVisible();
  await expect(structure.getByText("写正文的依据，模型每轮都会读取。")).toBeVisible();
  const chapterButton = page.locator(".writing-navigator").getByRole("button", { name: "第1章 · 雨夜", exact: true });
  await expect(chapterButton).toBeVisible();
  await chapterButton.click();
  await expect(page.getByText("侦探抵达旅馆。海面上没有灯。")).toBeVisible();
  await expect(page.getByRole("textbox", { name: "正文编辑" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /保存|续写|起草|方向建议/ })).toHaveCount(0);
  await shot(page, "02-readonly-chapter-and-chat");

  // 目录里的铅笔入口只改章节名；正文首行仍是旧标题时一起更新。
  await page.getByRole("button", { name: "修改章节名 第1章 · 雨夜", exact: true }).click();
  const renameDialog = page.getByRole("dialog", { name: "修改章节名", exact: true });
  await renameDialog.getByLabel("章节名").fill("夜航");
  await renameDialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".writing-navigator").getByRole("button", { name: "第1章 · 夜航", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "夜航", exact: true })).toBeVisible();

  // 章节列表可以收起，收起后只保留栏目名。
  const chaptersSummary = page.locator(".writing-nav-section > summary");
  await chaptersSummary.click();
  await expect(page.locator(".writing-navigator").getByRole("button", { name: "第1章 · 夜航", exact: true })).toBeHidden();
  await chaptersSummary.click();
  await expect(page.locator(".writing-navigator").getByRole("button", { name: "第1章 · 夜航", exact: true })).toBeVisible();

  // 侧栏模块区提示让模型补充长期资料；只填草稿，不发送。
  const extras = page.locator(".writing-nav-extras");
  if (!(await extras.evaluate(node => (node as HTMLDetailsElement).open))) await extras.locator("summary").click();
  await expect(page.getByText("人物小传、故事背景等长期资料，可以让模型判断并创建。", { exact: true })).toBeVisible();
  await chat.fill("");
  await page.getByRole("button", { name: "让模型补充资料", exact: true }).click();
  await expect(chat).toHaveValue("帮我判断这个故事还需要哪些长期资料，比如人物小传、故事背景；需要的话请创建并装入。");
  await expect(chat).toBeFocused();

  await page.setViewportSize({ width: 760, height: 800 });
  const chatButton = page.getByRole("button", { name: "对话", exact: true });
  await expect(chatButton).toBeVisible();
  await chatButton.click();
  await expect(page.locator(".chat-input")).toBeVisible();
  await expect(page.getByText("侦探抵达旅馆。海面上没有灯。")).toBeHidden();
});

test("章节列表里的正文文件可以直接修改首行标题", async ({ page, request }) => {
  const parent = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "books");
  mkdirSync(parent, { recursive: true });
  const root = join(mkdtempSync(join(parent, "writing-prose-rename-")), "旧稿作品");
  const opened = await request.post("/api/file-workspaces", { data: { path: root, create: true } });
  expect(opened.ok(), await opened.text()).toBeTruthy();
  const book = await opened.json();
  mkdirSync(join(root, "正文"), { recursive: true });
  writeFileSync(join(root, "正文", "0001 旧稿.md"), "# 旧稿\n\n原有正文。\n", "utf-8");

  await page.goto(`/#/workspaces/${book.id}/writing`);
  const entry = page.locator(".writing-navigator").getByRole("button", { name: "第一章 旧稿", exact: true });
  await expect(entry).toBeVisible();
  await page.getByRole("button", { name: "修改章节名 第一章 旧稿", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "修改章节名", exact: true });
  await dialog.getByLabel("章节名").fill("新稿");
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".writing-navigator").getByRole("button", { name: "第一章 新稿", exact: true })).toBeVisible();
  await page.locator(".writing-navigator").getByRole("button", { name: "第一章 新稿", exact: true }).click();
  await expect(page.getByRole("heading", { name: "新稿", exact: true })).toBeVisible();
});

test("章节较多时写作侧栏可以滚动，全书结构保持可见", async ({ page, request }) => {
  const parent = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "books");
  mkdirSync(parent, { recursive: true });
  const root = join(mkdtempSync(join(parent, "writing-scroll-")), "长篇");
  const opened = await request.post("/api/file-workspaces", { data: { path: root, create: true } });
  expect(opened.ok(), await opened.text()).toBeTruthy();
  const book = await opened.json();
  writeFileSync(join(root, "STRUCTURE.md"), "## 铁律\n\n测试用结构。\n", "utf-8");
  let planRevision = "empty";
  for (let index = 1; index <= 18; index += 1) {
    planRevision = seedChapter(book, `章节${index}`, `seed-chapter-${index}`, planRevision).plan_revision;
  }

  await page.goto(`/#/workspaces/${book.id}/writing`);
  const scroller = page.locator(".writing-nav-scroll");
  await expect(scroller).toBeVisible();
  await expect.poll(() => scroller.evaluate(node => node.scrollHeight - node.clientHeight)).toBeGreaterThan(0);
  await scroller.evaluate(node => { node.scrollTop = node.scrollHeight; });
  await expect.poll(() => scroller.evaluate(node => node.scrollTop)).toBeGreaterThan(0);

  const structure = page.locator(".writing-navigator").getByRole("button", { name: "全书结构", exact: true });
  await expect(structure).toBeInViewport();
  const extras = page.locator(".writing-nav-extras");
  if (!(await extras.evaluate(node => (node as HTMLDetailsElement).open))) await extras.locator("summary").click();
  await expect(structure).toBeInViewport();
});

test("修订面板显示段落差异，写作约定可编辑保存", async ({ page, request }) => {
  const parent = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "books");
  mkdirSync(parent, { recursive: true });
  const root = join(mkdtempSync(join(parent, "writing-revision-")), "作品");
  const opened = await request.post("/api/file-workspaces", {
    data: { path: root, create: true },
  });
  expect(opened.ok(), await opened.text()).toBeTruthy();
  const book = await opened.json();
  const base = `/api/file-workspaces/${book.id}`;
  const chapter = seedChapter(book, "雨夜", "e2e-revision-chapter");
  const draft = await request.put(`${base}/story/chapters/${chapter.chapter_id}/prose`, {
    data: {
      text: "# 雨夜\n\n林岚很紧张，她害怕许砚继续追问。\n\n窗外的雨没有停。\n",
      expected_digest: chapter.prose_digest,
      draft_revision: "e2e-revision-draft",
      request_key: "e2e-revision-draft",
    },
  });
  const draftBody = await draft.json();
  const edited = await request.put(`${base}/story/chapters/${chapter.chapter_id}/prose`, {
    data: {
      text: "# 雨夜\n\n林岚把杯沿转了半圈。「你刚才说到哪儿了？」\n\n窗外的雨没有停。\n",
      expected_digest: draftBody.prose_digest,
      draft_revision: "e2e-revision-edit",
      request_key: "e2e-revision-edit",
    },
  });
  expect(edited.ok(), await edited.text()).toBeTruthy();

  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.locator(".writing-navigator").getByRole("button", { name: "第1章 · 雨夜", exact: true }).click();
  // 回执只带回修订工作区，分析和差异都在「本章修订」里完成。
  const receipt = page.locator(".chat-shell");
  await expect(receipt.getByRole("button", { name: "查看差异", exact: true })).toBeVisible();
  await shot(page, "10-revision-receipt");
  await receipt.getByRole("button", { name: "查看差异", exact: true }).click();
  const drawer = receipt.getByRole("region", { name: "修订与写作约定" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("combobox", { name: "选择修订记录" })).toContainText("作者修订");
  await expect(drawer.getByText(/林岚把杯沿转了半圈/)).toBeVisible();
  await expect(drawer.getByText(/林岚很紧张/)).toBeVisible();
  await expect(drawer.getByRole("button", { name: "让模型分析" })).toBeEnabled();
  await shot(page, "08-revision-diff");

  await receipt.getByRole("tab", { name: "写作约定", exact: true }).click();
  const style = drawer.getByRole("textbox", { name: "写作约定" });
  await style.fill("- 写环境先写人物能感知的细节。\n");
  await drawer.getByRole("button", { name: "保存", exact: true }).click();
  await expect(drawer.getByText("写作约定已保存。", { exact: true })).toBeVisible();
  await shot(page, "09-style-document");
  await page.reload();
  await receipt.getByRole("tab", { name: "写作约定", exact: true }).click();
  await expect(drawer.getByRole("textbox", { name: "写作约定" })).toHaveValue(
    "- 写环境先写人物能感知的细节。\n",
  );

  // 用确定性的分析结果验证前端跟随真实任务状态，刷新后通过修订详情恢复日志。
  const analysis = {
    id: "analysis-e2e",
    status: "running",
    summary: "这句台词暂时只影响当前场景。",
    decisions: [],
    trace: { reads: [{ label: "正文修订", detail: "读取 1 处差异" }], steps: [] },
  };
  let started = 0;
  await page.route("**/story/revisions/*/analyses", async route => {
    started += 1;
    await route.fulfill({ json: analysis });
  });
  await page.route("**/story/revision-analyses/analysis-e2e", async route => {
    await route.fulfill({ json: { ...analysis, status: "ready" } });
  });
  await page.route("**/story/revisions/*?conversation_id=*", async route => {
    const response = await route.fetch();
    const detail = await response.json();
    await route.fulfill({ response, json: {
      ...detail, latest_analysis: started ? { ...analysis, status: "ready" } : null,
    } });
  });
  await receipt.getByRole("tab", { name: "本章修订", exact: true }).click();
  const analyze = drawer.getByRole("button", { name: "让模型分析", exact: true });
  await analyze.click();
  await expect(drawer.getByText("分析中…", { exact: true })).toBeVisible();
  await expect(drawer.getByText(analysis.summary)).toBeVisible();
  await drawer.getByText("查看运行日志", { exact: true }).click();
  await expect(drawer.getByText("读取 1 处差异")).toBeVisible();
  await page.reload();
  await receipt.getByRole("tab", { name: "本章修订", exact: true }).click();
  await expect(drawer.getByText(analysis.summary)).toBeVisible();
  expect(started).toBe(1);
});

test("稿纸修订模式：编辑、看差异、确认入稿，取消保留原文", async ({ page, request }) => {
  const parent = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "books");
  mkdirSync(parent, { recursive: true });
  const root = join(mkdtempSync(join(parent, "writing-revise-mode-")), "作品");
  const opened = await request.post("/api/file-workspaces", {
    data: { path: root, create: true },
  });
  expect(opened.ok(), await opened.text()).toBeTruthy();
  const book = await opened.json();
  const base = `/api/file-workspaces/${book.id}`;
  const chapter = seedChapter(book, "夜航", "e2e-mode-chapter");
  const seed = "林岚很紧张，她害怕许砚继续追问。\n\n窗外的雨没有停。\n";
  const draft = await request.put(`${base}/story/chapters/${chapter.chapter_id}/prose`, {
    data: {
      text: `# 夜航\n\n${seed}`,
      expected_digest: chapter.prose_digest,
      draft_revision: "e2e-mode-draft",
      request_key: "e2e-mode-draft",
    },
  });
  expect(draft.ok(), await draft.text()).toBeTruthy();

  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.locator(".writing-navigator").getByRole("button", { name: "第1章 · 夜航", exact: true }).click();
  await expect(page.getByText("林岚很紧张，她害怕许砚继续追问。")).toBeVisible();

  const chat = page.locator(".chat-shell");
  const revise = chat.getByRole("button", { name: "修改正文", exact: true });
  await revise.click();
  const editor = page.getByRole("textbox", { name: "正文编辑" });
  await expect(editor).toBeVisible();
  await expect(editor).toHaveValue(`# 夜航\n\n${seed}`);
  await shot(page, "11-revision-mode-edit");

  const rewritten = "# 夜航\n\n林岚把杯沿转了半圈。「你刚才说到哪儿了？」\n\n窗外的雨没有停。\n";
  await editor.fill(rewritten);
  await page.getByRole("button", { name: /看差异/ }).click();
  const diff = page.locator(".revision-diff");
  await expect(diff).toBeVisible();
  await expect(diff.locator("del").filter({ hasText: "林岚很紧张" })).toBeVisible();
  await expect(diff.locator("ins").filter({ hasText: "杯沿转了半圈" })).toBeVisible();
  await shot(page, "12-revision-mode-diff");

  await page.getByRole("button", { name: "确认修改", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "正文编辑" })).toHaveCount(0);
  await expect(page.getByText("林岚把杯沿转了半圈。「你刚才说到哪儿了？」")).toBeVisible();
  const viewRevision = chat.getByRole("button", { name: "查看差异", exact: true });
  await expect(viewRevision).toBeVisible();
  await shot(page, "13-revision-saved");

  const revisions = await request.get(`${base}/story/chapters/${chapter.chapter_id}/revisions`).then(r => r.json());
  expect(revisions.items.length).toBeGreaterThan(0);
  expect(revisions.items[0].source).toBe("author");

  await viewRevision.click();
  const drawer = chat.getByRole("region", { name: "修订与写作约定" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("combobox", { name: "选择修订记录" })).toContainText("作者修订");
  await expect(drawer.getByText(/林岚把杯沿转了半圈/)).toBeVisible();
  await chat.getByRole("tab", { name: "对话", exact: true }).click();

  // 取消保护：未确认的文字留在草稿里，取消需要二次确认；丢弃后正文保持确认过的版本。
  await revise.click();
  await page.getByRole("textbox", { name: "正文编辑" }).fill(`${rewritten}\n又加了一段。\n`);
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.getByText("这次修改还没有入稿。取消会丢弃它。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "继续编辑", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "正文编辑" })).toBeVisible();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "丢弃修改", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "正文编辑" })).toHaveCount(0);
  const reader = page.getByRole("region", { name: "写作", exact: true });
  await expect(reader.getByText("林岚把杯沿转了半圈。「你刚才说到哪儿了？」")).toBeVisible();
  await expect(reader.getByText("又加了一段。")).toHaveCount(0);

  // 编辑期间正文被别处更新：确认时进入冲突，保留草稿，覆盖后仍是一次作者修订。
  await revise.click();
  const mine = "# 夜航\n\n林岚没有回答，只把杯子放下。\n\n窗外的雨没有停。\n";
  await page.getByRole("textbox", { name: "正文编辑" }).fill(mine);
  const current = await request.get(`${base}/story/workbench?chapter_id=${chapter.chapter_id}`).then(r => r.json());
  const external = await request.put(`${base}/story/chapters/${chapter.chapter_id}/prose`, {
    data: {
      text: "# 夜航\n\n林岚把杯子放下，没有回答。\n\n窗外的雨没有停。\n",
      expected_digest: current.chapter.prose.digest,
      draft_revision: "e2e-mode-external",
      request_key: "e2e-mode-external",
    },
  });
  expect(external.ok(), await external.text()).toBeTruthy();
  await page.getByRole("button", { name: "确认修改", exact: true }).click();
  await expect(page.getByText("这一章在你编辑期间被更新了。你的文字仍保留在这里，请选择保留哪一份。", { exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "正文编辑" })).toHaveValue(mine);
  await page.getByRole("button", { name: "保留我的文字并覆盖", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "正文编辑" })).toHaveCount(0);
  await expect(reader.getByText("林岚没有回答，只把杯子放下。")).toBeVisible();
});
