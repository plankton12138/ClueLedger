import { test, expect } from "./fixtures/frontendExperience";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

test.beforeEach(async ({ book }) => {
  const root = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e");
  execFileSync("uv", ["run", "--directory", "..", "python", resolve("e2e/fixtures/clue_sources.py"), book.path, join(root, "state", "workspaces", book.id)], { windowsHide: true, stdio: "pipe" });
});

test("刷新后从服务端恢复章节与滚动位置", async ({ page, book, request }) => {
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "第一章 雨夜", exact: true }).click();
  const sheet = page.locator(".paper-sheet");
  await expect(sheet).toContainText("第二处");
  await sheet.evaluate(node => { node.scrollTop = 600; });
  await expect.poll(async () => {
    const response = await request.get(`/api/file-workspaces/${book.id}/reading-position`);
    return (await response.json()).offset;
  }).toBeGreaterThan(550);
  await page.reload();
  await expect(page.locator(".paper-toolbar h2")).toHaveText("第一章 雨夜");
  await expect.poll(() => sheet.evaluate(node => node.scrollTop)).toBeGreaterThan(550);
});

test("真实依据定位第二处重复引文，返回后保留草稿、阅读文件、章界和焦点", async ({ page, book }) => {
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "第二章 照片", exact: true }).click();
  await expect(page.locator(".paper-sheet")).toContainText("公开照片解释了背面内容");
  const draft = "检查遗书背面内容，保留这段尚未发送的作者要求。";
  await page.locator(".chat-input").fill(draft);
  await page.evaluate(hash => { location.hash = hash; }, `#/workspaces/${book.id}/materials/clues?chapter=1`);
  await page.getByRole("button", { name: /遗书背面内容/ }).click();
  const action = page.getByRole("button", { name: "定位原文", exact: true });
  await action.click();
  const marks = page.locator(".writing-paper mark[data-source-target]");
  await expect(marks).toHaveCount(1);
  await expect(marks).toHaveText("同一句线索。");
  await expect(marks).toBeInViewport();
  await expect(page.getByLabel("原文定位")).toContainText("任务截止章仍为第 2 章");
  await expect(page).toHaveURL(/as_of=1&task_chapter=2/);
  await expect(page.locator(".chat-input")).toHaveValue(draft);
  const output = resolve("../data/test-tmp/clue-sources"); mkdirSync(output, { recursive: true });
  await page.screenshot({ path: join(output, "source-1440.png") });
  await page.getByRole("button", { name: "返回资料", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "查看截至第几章" })).toHaveValue("1");
  await expect(action).toBeFocused();
  await page.getByRole("link", { name: "写作", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/workspaces/${book.id}/writing$`));
  await expect(page.getByLabel("原文定位")).toHaveCount(0);
  await expect(page.locator(".paper-sheet")).toContainText("公开照片解释了背面内容");
  await expect(page.locator(".chat-input")).toHaveValue(draft);
});

test("来源已改时保留旧引文并展示当前内容，重复句子不重新高亮", async ({ page, request, book }) => {
  const view = await request.get(`/api/file-workspaces/${book.id}/story-materials/clues?as_of_chapter=1`).then(response => response.json());
  const entry = view.entries.find((item: any) => item.title === "遗书背面内容");
  const id = entry.evidence[0].evidence_id;
  writeFileSync(join(book.path, "正文", "0001 雨夜.md"), "# 雨夜\n\n改稿后仍有同一句线索。\n", "utf8");
  await page.goto(`/#/workspaces/${book.id}/writing?source=${id}&as_of=1&task_chapter=2`);
  await expect(page.getByLabel("原文定位")).toContainText("来源已修改");
  await expect(page.getByLabel("原文定位")).toContainText("同一句线索。");
  await expect(page.locator(".paper-sheet")).toContainText("改稿后仍有同一句线索");
  await expect(page.locator(".writing-paper [data-source-target]")).toHaveCount(0);
});

test("定位同一篇文章后返回，恢复原来的阅读滚动位置", async ({ page, book }) => {
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "第一章 雨夜", exact: true }).click();
  const paper = page.locator(".paper-sheet");
  await expect(paper).toContainText("第二处");
  await paper.evaluate(node => { node.scrollTop = 432; });
  await expect.poll(() => paper.evaluate(node => node.scrollTop)).toBe(432);
  await page.evaluate(hash => { location.hash = hash; }, `#/workspaces/${book.id}/materials/clues?chapter=1`);
  await page.getByRole("button", { name: /遗书背面内容/ }).click();
  await page.getByRole("button", { name: "定位原文", exact: true }).click();
  await expect(page.locator(".writing-paper mark[data-source-target]")).toBeInViewport();
  await page.goBack();
  await expect(page.getByRole("combobox", { name: "查看截至第几章" })).toBeVisible();
  await page.getByRole("link", { name: "写作", exact: true }).click();
  await expect.poll(() => paper.evaluate(node => Math.round(node.scrollTop))).toBe(432);
});

test("实体和代码定位到所在段落，窄屏和150%字号下可关闭定位", async ({ page, request, book }) => {
  const view = await request.get(`/api/file-workspaces/${book.id}/story-materials/clues?as_of_chapter=1`).then(response => response.json());
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("combobox", { name: "调整字体大小" }).selectOption("150");
  await page.keyboard.press("Escape");
  for (const title of ["实体来源", "代码来源"]) {
    const id = view.entries.find((item: any) => item.title === title).evidence[0].evidence_id;
    await page.goto(`/#/workspaces/${book.id}/writing?source=${id}&as_of=1&task_chapter=2`);
    const paragraph = page.locator('.writing-paper [data-source-target="paragraph"]');
    await expect(paragraph).toHaveCount(1);
    await expect(paragraph).toBeInViewport();
    await expect(page.getByRole("status")).toContainText("已定位至所在段落");
    await expect(page.locator(".writing-paper mark")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    if (title === "代码来源") {
      const output = resolve("../data/test-tmp/clue-sources"); mkdirSync(output, { recursive: true });
      await page.screenshot({ path: join(output, "source-390-150.png") });
    }
    await page.getByRole("button", { name: "关闭定位", exact: true }).focus();
    await page.keyboard.press("Escape");
    await expect(page.getByLabel("原文定位")).toHaveCount(0);
  }
});

test("未来回收依据在早章范围不可读取，任意路径不能作为来源标识", async ({ request, book }) => {
  const late = await request.get(`/api/file-workspaces/${book.id}/story-materials/clues?as_of_chapter=2`).then(response => response.json());
  const entry = late.entries.find((item: any) => item.title === "遗书背面内容");
  const id = entry.evidence[1].evidence_id;
  const early = await request.get(`/api/file-workspaces/${book.id}/source-locations/${id}?as_of_chapter=1`);
  expect(early.status()).toBe(404);
  expect(await early.text()).not.toContain("公开照片解释了背面内容");
  expect((await request.get(`/api/file-workspaces/${book.otherId}/source-locations/${id}`)).status()).toBe(403);
});
