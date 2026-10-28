import { test, expect } from "./fixtures/frontendExperience";
import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const PLAN = "设定集/线索.md";
const QUOTE = "原计划：第三章让照片出现。";

test("从零建立线索、关联设定计划并保存推理目标", async ({ page, book, request }) => {
  mkdirSync(join(book.path, "设定集"), { recursive: true });
  writeFileSync(join(book.path, PLAN), `# 线索\n\n${QUOTE}\n`, "utf8");
  await page.goto(`/#/workspaces/${book.id}/materials/clues`);
  await page.getByRole("button", { name: "新建线索设计", exact: true }).click();
  const create = page.getByRole("dialog", { name: "新建线索设计", exact: true });
  await create.getByLabel("线索标题").fill("遗书背面的字");
  await create.getByRole("button", { name: "建立线索", exact: true }).click();

  const detail = page.locator(".materials-evidence .clue-detail");
  await expect(detail.getByRole("heading", { name: "遗书背面的字", exact: true })).toBeVisible();
  await expect(detail).toContainText("还没有可引用的正文记录");
  await detail.locator("summary").filter({ hasText: "作者计划" }).click();
  await detail.getByRole("button", { name: "关联计划", exact: true }).click();

  const picker = page.getByRole("dialog", { name: "关联作者计划", exact: true });
  await picker.getByRole("button", { name: "作品设定", exact: true }).click();
  await picker.getByRole("button").filter({ hasText: PLAN }).click();
  const source = picker.getByLabel("计划原文").locator("pre");
  await source.evaluate((node, quote) => {
    const full = node.textContent ?? "";
    const start = full.indexOf(quote);
    const text = node.firstChild as Text;
    const range = document.createRange();
    range.setStart(text, start);
    range.setEnd(text, start + quote.length);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
  }, QUOTE);
  await source.dispatchEvent("keyup");
  await expect(picker).toContainText(`已选：${QUOTE}`);
  await picker.getByLabel("关系说明").fill("第三章让照片出现");
  await picker.getByRole("button", { name: "关联到线索", exact: true }).click();

  await expect(detail).toContainText("计划来源有效");
  await expect(detail).toContainText(QUOTE);
  await expect(detail).toContainText("关系：第三章让照片出现");

  await detail.getByRole("button", { name: "新建推理", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "新建推理目标", exact: true });
  await editor.getByLabel("目标结论").fill("林岚提前接触过遗书原件");
  await editor.getByLabel("必要前提（每行一条）").fill("林岚能说出背面内容");
  await editor.getByLabel("其他解释（每行一条）").fill("警方此前公开过背面照片");
  await editor.locator("label").filter({ hasText: "第三章让照片出现" }).locator("input[type=checkbox]").check();
  await editor.getByRole("button", { name: "保存目标", exact: true }).click();

  await expect(detail).toContainText("林岚提前接触过遗书原件");
  await expect(detail).toContainText("推出结论");
  await expect(detail).toContainText("林岚能说出背面内容");
  await expect(detail).toContainText("警方此前公开过背面照片");
  await expect(detail).toContainText("计划依据");
  await expect(detail).not.toContainText("章埋设");

  const assembly = await request
    .get(`/api/file-workspaces/${book.id}/assembly`)
    .then(response => response.json());
  const revision = await request
    .get(`/api/module-library/modules/${book.moduleId}/revisions/${book.revisionId}`)
    .then(response => response.json());
  const bound = await request.put(`/api/file-workspaces/${book.id}/assembly`, {
    data: {
      bindings: [{
        module: {
          library_id: revision.library_id,
          module_id: book.moduleId,
          revision_id: book.revisionId,
        },
      }],
      expected_digest: assembly.digest,
      request_key: `bind-s4-${book.id}`,
    },
  });
  expect(bound.ok(), await bound.text()).toBeTruthy();
  const moduleQuote = "未停的雨🌧️与侦探𠮷。";
  await detail.getByRole("button", { name: "关联计划", exact: true }).click();
  const modulePicker = page.getByRole("dialog", { name: "关联作者计划", exact: true });
  await modulePicker.getByRole("button", { name: "已装模块", exact: true }).click();
  await modulePicker.getByRole("button", { name: /长记录/ }).click();
  await modulePicker.getByRole("button", { name: /雨夜原文/ }).click();
  const moduleSource = modulePicker.getByLabel("计划原文").locator("pre");
  await expect(moduleSource).toContainText(moduleQuote);
  await moduleSource.evaluate((node, quote) => {
    const full = node.textContent ?? "";
    const start = full.indexOf(quote);
    const text = node.firstChild as Text;
    const range = document.createRange();
    range.setStart(text, start);
    range.setEnd(text, start + quote.length);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
  }, moduleQuote);
  await moduleSource.dispatchEvent("keyup");
  await expect(modulePicker).toContainText(`已选：${moduleQuote}`);
  await modulePicker.getByRole("button", { name: "关联到线索", exact: true }).click();
  await expect(detail).toContainText(moduleQuote);
  await expect(detail).toContainText("计划来源有效");

  const output = resolve("../dev-notes/v1.0/artifacts/s4");
  mkdirSync(output, { recursive: true });
  const pane = page.locator(".materials-evidence");
  await pane.evaluate(node => { node.scrollTop = node.scrollHeight; });
  await page.screenshot({ path: join(output, "clue-design-targets-1440.png") });
  await pane.evaluate(node => { node.scrollTop = 0; });
  await page.screenshot({ path: join(output, "clue-design-1440.png") });
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("combobox", { name: "调整字体大小" }).selectOption("150");
  await page.keyboard.press("Escape");
  for (const width of [1024, 640, 390]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await expect(detail.getByRole("heading", { name: "遗书背面的字", exact: true })).toBeVisible();
  await page.screenshot({ path: join(output, "clue-design-390-150.png") });
});
