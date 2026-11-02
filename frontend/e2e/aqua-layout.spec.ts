import { test, expect, type APIRequestContext, type Page } from "@playwright/test";
import { mkdirSync, mkdtempSync } from "node:fs";
import { join, resolve } from "node:path";

const output = resolve(process.env.HASTINGS_E2E_SCREENSHOT_DIR ?? "../dev-notes/v1.0.9/artifacts/aqua");

async function shot(page: Page, name: string) {
  mkdirSync(output, { recursive: true });
  await page.screenshot({ path: join(output, `${name}.png`) });
}

async function seed(request: APIRequestContext, root: string) {
  const opened = await request.post("/api/file-workspaces", { data: { path: root, create: true } });
  expect(opened.ok(), await opened.text()).toBeTruthy();
  const book = await opened.json();
  const base = `/api/file-workspaces/${book.id}`;
  async function send(method: "PUT" | "POST", path: string, data: object) {
    const response = await request.fetch(`${base}${path}`, { method, data });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }
  const intent = await send("PUT", "/story/book-intent", {
    text: "暴雪封山，旅馆里少了一名住客。",
    expected_plan_revision: "empty",
    request_key: "aqua-intent",
  });
  const chapter = await send("POST", "/story/chapters", {
    title: "雨夜",
    expected_plan_revision: intent.plan_revision,
    request_key: "aqua-chapter",
  });
  await send("PUT", `/story/chapters/${chapter.chapter_id}/prose`, {
    text: "# 雨夜\n\n" + [
      "雪把山路封死的时候，林砚刚走进旅馆。前台的人说，今晚只剩一间房。",
      "壁炉里的火钳上留着一层蓝蜡，像有人在这里烧过不该烧的东西。",
      "管家站在楼梯口，重复说自己整晚都在房间里。没有人问他，为什么要先解释。",
      "林砚看见他袖口的水迹，顺着指尖滴在木地板上。",
    ].join("\n\n"),
    expected_digest: chapter.prose_digest,
    draft_revision: "aqua-draft",
    request_key: "aqua-prose",
  });
  return { id: book.id, chapterId: chapter.chapter_id as string };
}

function style(locator: ReturnType<Page["locator"]>, property: string) {
  return locator.evaluate((node, name) => getComputedStyle(node).getPropertyValue(name as string), property);
}

test("Aqua 材质、控件状态与响应式写作页", async ({ page, request }) => {
  const parent = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "books");
  mkdirSync(parent, { recursive: true });
  const root = join(mkdtempSync(join(parent, "aqua-")), "雪夜旅馆");
  const { id, chapterId } = await seed(request, root);
  await page.goto(`/#/workspaces/${id}/writing?chapter=${chapterId}`);
  const chat = page.getByRole("button", { name: "对话", exact: true });
  if (await chat.getAttribute("aria-pressed") === "true") await chat.click();
  await expect(page.getByRole("textbox", { name: "正文编辑" })).toBeVisible();
  await expect(page.locator(".chapter-margin-inline")).toBeVisible();
  // V04：页边索引属于稿纸内的第二列，对话不在同一栅格里，不额外多出第四栏。
  await expect(page.locator(".chapter-sheet-grid > *")).toHaveCount(2);
  await expect(page.getByRole("complementary", { name: "本章信息" })).toHaveCount(1);

  // 银灰 chrome 与浅色稿纸分层；正文保持平整。
  const header = page.locator(".app-header");
  expect(await style(header, "background-image")).toContain("linear-gradient");
  expect(await style(header, "background-image")).toContain("repeating-linear-gradient");
  const paper = page.locator(".writing-paper");
  expect(await style(paper, "background-color")).toBe("rgb(255, 253, 248)");
  const editor = page.getByRole("textbox", { name: "正文编辑" });
  expect(await style(editor, "color")).toBe("rgb(48, 52, 58)");

  const button = page.getByRole("button", { name: "侧栏", exact: true });
  expect(await style(button, "background-image")).toContain("linear-gradient");
  expect(parseFloat(await style(button, "border-top-left-radius"))).toBeGreaterThanOrEqual(16);
  const selectedLine = page.locator(".writing-navigator .toc-line:has(.toc-row.on)");
  expect(await style(selectedLine, "background-color")).toBe("rgb(220, 232, 248)");
  expect(await style(selectedLine, "background-image")).toBe("none");
  await button.hover();
  expect(await style(button, "border-color")).toBe("rgb(40, 98, 173)");
  await page.keyboard.press("Tab");
  await button.focus();
  expect(await style(button, "outline-color")).toBe("rgb(36, 101, 181)");
  const save = page.getByRole("button", { name: "保存", exact: true });
  expect(await style(save, "background-color")).toBe("rgb(227, 231, 232)");
  await page.getByRole("textbox", { name: "正文编辑" }).click();
  await shot(page, "01-writing-1440");

  // 对话框标题区使用同一 chrome token，正文区平整。
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "设置", exact: true });
  await expect(dialog).toBeVisible();
  expect(await style(dialog.locator(".ui-dialog-header"), "background-image")).toContain("linear-gradient");
  await shot(page, "02-settings-dialog");
  await page.keyboard.press("Escape");

  // 减少动态效果时不保留循环动画或长过渡。
  await page.emulateMedia({ reducedMotion: "reduce" });
  expect(["0.01ms", "1e-05s"]).toContain(await style(button, "transition-duration"));
  await page.emulateMedia({ reducedMotion: "no-preference" });

  async function font(scale: number) {
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await page.getByRole("dialog", { name: "设置", exact: true }).getByLabel("调整字体大小").selectOption(String(scale));
    await page.keyboard.press("Escape");
  }
  for (const [width, scale] of [[1024, 100], [760, 100], [640, 150], [390, 150], [720, 100]] as const) {
    await page.setViewportSize({ width, height: 900 });
    await font(scale);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const index = page.getByRole("button", { name: "本章信息", exact: true });
    if (await index.isVisible() && await index.isEnabled()) {
      await index.click();
      await expect(page.getByRole("region", { name: "本章信息" })).toBeVisible();
      await shot(page, `03-index-${width}-${scale}`);
      await index.click();
    }
    await shot(page, `04-writing-${width}-${scale}`);
  }
});
