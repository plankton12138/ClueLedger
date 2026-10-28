import { test, expect } from "./fixtures/frontendExperience";
import type { Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const output = resolve(process.env.HASTINGS_E2E_SCREENSHOT_DIR ?? "../dev-notes/v1.0/artifacts/aqua-ui");
async function shot(page: Page, name: string) {
  mkdirSync(output, { recursive: true });
  await page.screenshot({ path: join(output, `${name}.png`) });
}
async function font(page: Page, scale: number) {
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("dialog", { name: "设置", exact: true }).getByLabel("调整字体大小").selectOption(String(scale));
  await page.keyboard.press("Escape");
}

test("Aqua 写作布局、翻页和字号操作保留复古气泡及可达的发送位置", async ({ page, book }) => {
  writeFileSync(join(book.path, "正文", "0001 雨夜.md"), "# 雨夜\n\n" + [
    "雨还在下，港口的雾气被风吹散，又很快聚拢起来。窗外传来潮水拍打堤岸的闷响。",
    "林岚没有碰那封遗书。她站在桌边，忽然说：背面的最后一行，是写给我的。",
    "沈砚抬起头，看向她。窗外的雨声更密了，他放下手中的笔，问道：你怎么知道？",
    "林岚没有立即回答，只是望着窗外。远处的灯塔在雾中一明一暗。",
    "沈砚把遗书翻了过来。那一行字藏在折痕里，墨迹比正面更淡。",
    "字迹依旧是那种冷静而克制的笔触。没有署名，只有一个称呼，和一句几乎被水迹晕开的告别。",
  ].join("\n\n"), "utf8");
  writeFileSync(join(book.path, "正文", "0002 照片.md"), "# 照片\n\n他们找到了那张旧照片。", "utf8");
  // 稳定消息仅用于验证气泡；文件和目录仍经真实服务读取，不调用模型。
  const at = "2026-10-28T12:00:00+08:00", id = "aqua_preview";
  const item = { id, title: "线索设计", created_at: at, updated_at: at };
  await page.route(`**/api/file-workspaces/${book.id}/conversations`, route => route.fulfill({ json: { items: [item], recent_id: id, active_turn: null, revision: "r1" } }));
  await page.route(`**/api/file-workspaces/${book.id}/conversation-ui`, route => route.fulfill({ json: { recent_id: id, drafts: {}, revision: "r1" } }));
  await page.route(`**/api/file-workspaces/${book.id}/conversations/${id}/messages?*`, route => route.fulfill({ json: {
    events: [
      { seq: 1, at, type: "turn_started", turn_id: "t_aqua", text: "让林岚提前知道遗书背面的内容，但先保留其他解释。" },
      { seq: 2, at, type: "assistant_started", turn_id: "t_aqua", message_id: "m_aqua" },
      { seq: 3, at, type: "assistant_block_started", turn_id: "t_aqua", message_id: "m_aqua", block_id: "b_aqua", kind: "text", order: 0 },
      { seq: 4, at, type: "assistant_block_finished", turn_id: "t_aqua", message_id: "m_aqua", block_id: "b_aqua", kind: "text", text: "可以先让她说出背面的句子，再让沈砚追问消息来源。\n\n> 她说得太快，像是早已读过。\n\n这一处只引起怀疑。照片、转述等来源仍需在后续排除。", last_part: -1, completion: "completed", duration_ms: 100 },
      { seq: 5, at, type: "assistant_finished", turn_id: "t_aqua", message_id: "m_aqua", completion: "completed" },
      { seq: 6, at, type: "run_finished", turn_id: "t_aqua", status: "completed", recording: "no_change", changed_paths: [], commit: null, note: "" },
    ], last_seq: 6, active_turn: null, revision: "r1",
  } }));
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "第一章 雨夜", exact: true }).click();
  await expect(page.locator(".writing-layout")).toHaveAttribute("data-layout", "three");
  await expect(page.getByRole("navigation", { name: "作品页面" }).getByRole("link")).toHaveCount(3);
  await expect(page.locator(".chat-msg.mine .chat-bubble")).toContainText("让林岚");
  expect(await page.locator(".chat-msg.mine .chat-bubble").evaluate(el => getComputedStyle(el).backgroundColor)).toBe("rgb(150, 221, 65)");
  expect(await page.locator(".chat-reply").evaluate(el => getComputedStyle(el).backgroundColor)).toBe("rgb(214, 214, 216)");
  expect(await page.locator(".prose-paper p").first().evaluate(el => parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(20);
  await page.getByRole("button", { name: "下一章", exact: true }).click();
  await expect(page.locator(".paper-sheet")).toContainText("他们找到了那张旧照片");
  await page.getByRole("button", { name: "上一章", exact: true }).click();
  await page.getByRole("button", { name: "增大字号", exact: true }).click();
  await expect.poll(() => page.locator(".prose-paper").evaluate(el => parseFloat(getComputedStyle(el).fontSize))).toBeCloseTo(23.1);
  await page.getByRole("button", { name: "减小字号", exact: true }).click();
  await shot(page, "01-writing-1440");
  for (const [width, scale] of [[1024, 100], [640, 150], [390, 150]]) {
    await page.setViewportSize({ width, height: 900 }); await font(page, scale);
    if (!await page.locator(".chat-input").isVisible()) await page.getByRole("button", { name: "对话", exact: true }).click();
    await expect(page.getByRole("button", { name: "发送", exact: true })).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await shot(page, `writing-${width}-${scale}`);
  }
});

test("模块列表与预览并列，缩窄和放大字号保留草稿及主操作", async ({ page, book }) => {
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.locator(".chat-input").fill("尚未发送的写作要求");
  await page.getByRole("link", { name: "模块库", exact: true }).click();
  const library = page.getByRole("region", { name: "模块库", exact: true });
  const browser = library.getByRole("region", { name: "浏览模块库", exact: true });
  await browser.getByRole("button", { name: `海上城市-${book.id}`, exact: true }).click();
  await expect(browser).toBeVisible();
  const detail = library.getByRole("region", { name: "模块详情", exact: true });
  await expect(detail).toContainText("终年起雾。");
  const listBox = (await browser.boundingBox())!, detailBox = (await detail.boundingBox())!;
  expect(detailBox.x).toBeGreaterThanOrEqual(listBox.x + listBox.width - 1);
  await expect(detail.getByRole("button", { name: "添加到作品", exact: true })).toBeInViewport();
  await shot(page, "03-library-1440");
  await detail.getByRole("button", { name: "编辑", exact: true }).click();
  const body = detail.getByLabel("模块正文").last();
  await body.fill("缩窄窗口后仍保留这份模块草稿。");
  for (const [width, scale] of [[1024, 100], [640, 150], [390, 150]]) {
    await page.setViewportSize({ width, height: 900 }); await font(page, scale);
    await expect(browser).toBeHidden();
    await expect(body).toHaveValue("缩窄窗口后仍保留这份模块草稿。");
    await expect(detail.getByRole("button", { name: "保存", exact: true })).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await shot(page, `library-${width}-${scale}`);
  }
  await page.setViewportSize({ width: 1440, height: 900 }); await font(page, 100);
  await expect(browser).toBeVisible();
  await expect(body).toHaveValue("缩窄窗口后仍保留这份模块草稿。");
  await detail.getByRole("button", { name: "取消", exact: true }).click();
  await library.getByRole("button", { name: "返回写作", exact: true }).click();
  await expect(page.locator(".chat-input")).toHaveValue("尚未发送的写作要求");
});

test("故事资料入口恢复浏览位置，线索依据在宽屏并列、窄屏可返回", async ({ page, book }) => {
  const data = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e");
  execFileSync("uv", ["run", "--directory", "..", "python", resolve("e2e/fixtures/chapter_clues.py"), book.path, join(data, "state", "workspaces", book.id)], { windowsHide: true, stdio: "pipe" });
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("link", { name: "故事资料", exact: true }).click();
  await page.getByRole("link", { name: "线索与伏笔", exact: true }).click();
  await page.locator(".materials-list").getByRole("button", { name: /信封背面的字/ }).click();
  const evidence = page.locator(".materials-evidence");
  await expect(evidence).toContainText("信封背面有字。");
  await expect(page.locator(".materials-list")).toBeVisible();
  await shot(page, "02-clues-1440");
  const location = page.url();
  await page.getByRole("link", { name: "写作", exact: true }).click();
  await page.getByRole("link", { name: "故事资料", exact: true }).click();
  await expect(page).toHaveURL(location);
  await evidence.getByRole("button", { name: "定位原文", exact: true }).first().click();
  await expect(page.getByLabel("原文定位")).toBeVisible();
  await page.getByRole("button", { name: "下一章", exact: true }).click();
  await expect(page.getByLabel("原文定位")).toHaveCount(0);
  await expect(page.locator(".paper-sheet")).toContainText("照片证明她看过背面");
  await page.getByRole("link", { name: "故事资料", exact: true }).click();
  await expect(page).toHaveURL(location);
  await page.setViewportSize({ width: 390, height: 900 }); await font(page, 150);
  await expect(evidence).toBeVisible();
  await expect(page.getByRole("button", { name: "返回资料列表", exact: true })).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await shot(page, "clues-390-150");
});
