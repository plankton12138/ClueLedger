import { test, expect, type APIRequestContext, type Page } from "@playwright/test";
import { mkdirSync, mkdtempSync } from "node:fs";
import { join, resolve } from "node:path";

/**
 * 在线模型验收 O01—O09（1.0.9 P7）。
 *
 * 只断言可判定的事实：候选是否旁置、作者输入是否被覆盖、正式数据是否在采用前发生变化。
 * 模型回复的具体措辞不断言。每条用例自建隔离作品，顺序执行，失败后只单例复验。
 */
test.describe.configure({ mode: "serial" });
test.setTimeout(300_000);

const parent = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "books");
mkdirSync(parent, { recursive: true });

async function newBook(request: APIRequestContext, name: string) {
  const root = join(mkdtempSync(join(parent, "online-")), name);
  const opened = await request.post("/api/file-workspaces", { data: { path: root, create: true } });
  expect(opened.ok(), await opened.text()).toBeTruthy();
  const book = await opened.json();
  return { id: book.id as string, base: `/api/file-workspaces/${book.id}` };
}

async function seedChapter(request: APIRequestContext, base: string) {
  const intent = await request.put(`${base}/story/book-intent`, {
    data: {
      text: "暴雪封山的旅馆发生命案，林砚从湿手套开始怀疑管家；管家的话未必是真的。",
      expected_plan_revision: "empty",
      request_key: "online-intent",
    },
  });
  expect(intent.ok(), await intent.text()).toBeTruthy();
  const chapter = await request.post(`${base}/story/chapters`, {
    data: {
      title: "第一章",
      expected_plan_revision: (await intent.json()).plan_revision,
      request_key: "online-chapter",
    },
  });
  expect(chapter.ok(), await chapter.text()).toBeTruthy();
  const body = await chapter.json();
  return { chapterId: body.chapter_id as string, digest: body.prose_digest as string, path: body.path as string };
}

async function saveProse(request: APIRequestContext, base: string, chapterId: string, text: string, digest: string, key: string) {
  const saved = await request.put(`${base}/story/chapters/${chapterId}/prose`, {
    data: { text, expected_digest: digest, draft_revision: key, request_key: key },
  });
  expect(saved.ok(), await saved.text()).toBeTruthy();
  return (await saved.json()).prose_digest as string;
}

async function candidateReady(page: Page, key: string, status = "ready") {
  const candidate = page.locator(`[data-assist-key="${key}"] .model-candidate`);
  await expect(candidate).toHaveAttribute("data-status", status, { timeout: 240_000 });
  return candidate;
}

test("O01 空作品交给模型构思：候选不自动进入正式构思", async ({ page, request }) => {
  const book = await newBook(request, "O01");
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await expect(page.getByRole("heading", { name: "全书构思", exact: true })).toBeVisible();

  const intent = page.getByRole("textbox", { name: "作品意图" });
  await expect(intent).toHaveValue("");
  await page.getByRole("button", { name: "交给模型构思" }).click();
  const candidate = await candidateReady(page, "book_intent:book");
  await expect(candidate.locator(".model-candidate-text")).not.toBeEmpty();
  await expect(intent).toHaveValue("");

  await candidate.getByRole("button", { name: "采用到输入" }).click();
  await expect(intent).not.toHaveValue("");
});

test("O02 空章看看方向建议：只读参考，不写正文", async ({ page, request }) => {
  const book = await newBook(request, "O02");
  const chapter = await seedChapter(request, book.base);
  await page.goto(`/#/workspaces/${book.id}/writing?chapter=${chapter.chapterId}`);
  const editor = page.getByRole("textbox", { name: "正文编辑" });
  await expect(editor).toHaveValue("");

  await page.getByRole("button", { name: "看看方向建议" }).click();
  const candidate = await candidateReady(page, `direction:${chapter.chapterId}`);
  await expect(candidate.locator(".model-candidate-text")).not.toBeEmpty();
  await expect(candidate.getByRole("button", { name: "复制建议" })).toBeVisible();
  await expect(editor).toHaveValue("");
  await expect(page.getByText("方向建议只是参考文字")).toBeVisible();
});

test("O03 空章由模型起草：候选不自动写入正文", async ({ page, request }) => {
  const book = await newBook(request, "O03");
  const chapter = await seedChapter(request, book.base);
  await page.goto(`/#/workspaces/${book.id}/writing?chapter=${chapter.chapterId}`);
  const editor = page.getByRole("textbox", { name: "正文编辑" });
  await expect(editor).toHaveValue("");

  await page.getByRole("button", { name: "由模型起草" }).click();
  const candidate = await candidateReady(page, `prose:${chapter.chapterId}`);
  await expect(candidate.locator(".model-candidate-text")).not.toBeEmpty();
  await expect(editor).toHaveValue("");
});

test("O04 起草期间作者继续手写：作者稿不被覆盖", async ({ page, request }) => {
  const book = await newBook(request, "O04");
  const chapter = await seedChapter(request, book.base);
  await page.goto(`/#/workspaces/${book.id}/writing?chapter=${chapter.chapterId}`);
  const editor = page.getByRole("textbox", { name: "正文编辑" });
  await page.getByRole("button", { name: "由模型起草" }).click();
  await editor.fill("林砚先自己写了一段：门外的雪已经齐膝。");
  // 起草期间作者改了正文，候选依据的草稿已过期：保留候选但标记 stale。
  const candidate = await candidateReady(page, `prose:${chapter.chapterId}`, "stale");
  await expect(candidate).toContainText("生成后内容又变了");
  await expect(editor).toHaveValue("林砚先自己写了一段：门外的雪已经齐膝。");
});

test("O05 已有正文让模型续写：候选不覆盖当前正文", async ({ page, request }) => {
  const book = await newBook(request, "O05");
  const chapter = await seedChapter(request, book.base);
  const text = "# 第一章\n\n林砚走进旅馆，前台说今晚只剩一间房。\n";
  await saveProse(request, book.base, chapter.chapterId, text, chapter.digest, "online-o05");
  await page.goto(`/#/workspaces/${book.id}/writing?chapter=${chapter.chapterId}`);
  const editor = page.getByRole("textbox", { name: "正文编辑" });
  await expect(editor).toHaveValue(/林砚走进旅馆/);

  await page.getByRole("button", { name: "让模型续写" }).click();
  const candidate = await candidateReady(page, `prose:${chapter.chapterId}`);
  await expect(candidate.locator(".model-candidate-text")).not.toBeEmpty();
  await expect(editor).toHaveValue(/林砚走进旅馆/);
});

test("O06 手写计划外细节：只形成候选及准确原句", async ({ page, request }) => {
  const book = await newBook(request, "O06");
  const chapter = await seedChapter(request, book.base);
  const text =
    "# 第一章\n\n林砚走进旅馆。\n\n壁炉里的火钳上留着一层蓝蜡，像有人烧过不该烧的东西。\n";
  const digest = await saveProse(request, book.base, chapter.chapterId, text, chapter.digest, "online-o06");
  await page.goto(`/#/workspaces/${book.id}/writing?chapter=${chapter.chapterId}`);
  const margin = page.locator(".chapter-margin-inline");
  await expect(margin).toContainText("正文新增", { timeout: 240_000 });
  await expect(margin).toContainText("蓝蜡");

  const workbench = await (await request.get(`${book.base}/story/workbench?chapter_id=${chapter.chapterId}`)).json();
  const items = workbench.chapter.index.items as { placement: string; occurrences: { quote: string }[] }[];
  const observed = items.filter(row => row.placement === "observed_candidate");
  expect(observed.length).toBeGreaterThan(0);
  expect(observed.some(row => row.occurrences.some(occ => occ.quote.includes("蓝蜡")))).toBe(true);
  expect(digest).toBeTruthy();
});


test("O07 已入账信息与正文矛盾：进入需要对齐", async ({ page, request }) => {
  const book = await newBook(request, "O07");
  const chapter = await seedChapter(request, book.base);
  const text = "# 第一章\n\n林砚走进旅馆。\n\n壁炉里的火钳上留着一层蓝蜡。\n";
  await saveProse(request, book.base, chapter.chapterId, text, chapter.digest, "online-o07");
  const url = `/#/workspaces/${book.id}/writing?chapter=${chapter.chapterId}`;
  // 静态服务偶发在导航瞬间返回 404：确认编辑器已挂载再继续，不让环境抖动伪装成语义失败。
  await expect(async () => {
    await page.goto(url);
    await expect(page.getByRole("textbox", { name: "正文编辑" })).toBeVisible({ timeout: 5_000 });
  }).toPass({ timeout: 60_000 });
  const chat = page.getByRole("button", { name: "对话", exact: true });
  if ((await chat.getAttribute("aria-pressed")) === "true") await chat.click();
  const margin = page.locator(".chapter-margin-inline");
  await expect(margin).toBeVisible();
  await expect(margin).toContainText("正文新增", { timeout: 240_000 });

  // 入账与「蓝蜡」有关的那条候选；先把无关候选入账会让矛盾检测失去对照。
  const candidateList = margin.locator("section", { hasText: "正文新增" }).locator("li");
  const wax = candidateList.filter({ hasText: "蜡" });
  const target = (await wax.count()) > 0 ? wax.first() : candidateList.first();
  await target.getByRole("button", { name: "确认入账" }).click();
  await expect(margin).toContainText("已经写入", { timeout: 60_000 });

  const editor = page.getByRole("textbox", { name: "正文编辑" });
  await editor.fill("# 第一章\n\n林砚走进旅馆。\n\n壁炉里的火钳上留着一层红蜡。\n");
  await expect(page.locator(".chapter-editor-status")).toContainText("已保存", { timeout: 30_000 });
  await expect(margin).toContainText("需要对齐", { timeout: 240_000 });
});

test("O09 读者视角：回复不泄露尚未写出的真相", async ({ page, request }) => {
  const book = await newBook(request, "O09");
  const chapter = await seedChapter(request, book.base);
  const text =
    "# 第一章\n\n林砚走进旅馆，前台说今晚只剩一间房。\n\n" +
    "管家站在楼梯口，重复说自己整晚都在房间里。\n";
  await saveProse(request, book.base, chapter.chapterId, text, chapter.digest, "online-o09");
  await page.goto(`/#/workspaces/${book.id}/writing?chapter=${chapter.chapterId}`);

  const chat = page.getByRole("button", { name: "对话", exact: true });
  if ((await chat.getAttribute("aria-pressed")) !== "true") await chat.click();
  const input = page.getByRole("textbox", { name: /说要求，回车发送/ });
  await input.fill("只以第一章读者的视角，列出目前能确认的事实和疑点。不要补充后面章节才会揭示的内容。");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  const reply = page.locator('[data-role="assistant"]').last();
  await expect(reply).toBeVisible({ timeout: 240_000 });
  await expect(reply).not.toBeEmpty();
  const answer = await reply.innerText();
  expect(answer.length).toBeGreaterThan(10);
  expect(answer).not.toContain("搬尸");
  expect(answer).not.toContain("洗手套");
});
