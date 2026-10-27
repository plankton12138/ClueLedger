import { test, expect, type APIRequestContext, type Page } from "@playwright/test";
import { mkdtempSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { ReviewReport } from "../src/api/workspace";

/**
 * 在线模型浏览器验收（0.6 V06-D2）。
 *
 * 与 `workspace.spec.ts` 的区别：这里真的调用模型，检查「作者说话 → 模型登记范围 →
 * 写入 → 检查」在浏览器里是否接通。断言只放在可判定的事实上：哪些文件变了、禁止文件
 * 的指纹是否没变、事件与页面是否展示范围与完整报告、草稿参考是否恢复、索引是否过期。
 * 文字好不好不在自动判断范围内，
 * 由作者看样例。
 *
 * 首次失败直接留证，不重试；端点与工具错误以外先记为待判读，
 * 再核对脚本、夹具、产品接线或模型理解，分类不改变失败本身。
 */

const PORT = process.env.HASTINGS_E2E_PORT ?? "8931";
const BASE = `http://127.0.0.1:${PORT}/api/file-workspaces`;
const ARTIFACTS = join(process.cwd(), "..", "data", "test-tmp", "e2e-model");

const CHAPTER = "正文/0003 书房.md";
const FIRST = "正文/0001 雨夜.md";
const OTHER = "正文/0002 走廊.md";
const TRUTH = "设定集/真相.md";

const CHAPTER_TEXT = "# 书房\n\n侦探推开门，桌上有一盏冷掉的茶。\n窗外雪停了。\n";
const OTHER_TEXT = "# 走廊\n\n走廊尽头的灯忽明忽暗。\n";
const TRUTH_TEXT = "# 真相\n\n凶手是管家。\n";

const SCOPE_PROMPT =
  "把第三章《书房》里「桌上有一盏冷掉的茶」改成「桌上那盏茶还冒着热气」。只改这一章，" +
  "设定集/真相.md 不要动。";
const REVIEW_PROMPT =
  "把第三章《书房》里「窗外雪停了」改成「窗外又飘起雪」。改完检查有没有前后矛盾。";

let bindingId = "";
let workspaceRoot = "";

type WorkspaceEvent = Record<string, unknown> & { type?: string };

test.beforeAll(async ({ request }) => {
  mkdirSync(ARTIFACTS, { recursive: true });
  const response = await request.get(`http://127.0.0.1:${PORT}/api/model-connections`);
  expect(response.ok()).toBeTruthy();
  const config = await response.json();
  const connection = config.connections.find(
    (item: { name: string }) => item.name === config.selection.connection_id,
  );
  expect(connection, "在线验收必须有当前模型连接").toBeTruthy();
  expect(["localhost", "127.0.0.1", "[::1]", "0.0.0.0"]).not.toContain(
    new URL(connection.base_url).hostname,
  );
});

test.afterEach(async ({ page, request }, info) => {
  if (info.status === info.expectedStatus) return;
  const conversations = await request.get(`${BASE}/${bindingId}/conversations`);
  const listed = conversations.ok() ? await conversations.json() : { items: [] };
  const conversation = listed.items[0]?.id;
  const snapshot = conversation
    ? await request.get(`${BASE}/${bindingId}/conversations/${conversation}/messages`)
    : null;
  const events = snapshot?.ok() ? (await snapshot.json()).events : [];
  saveEvidence("first-failure", {
    title: info.title, errors: info.errors.map((error) => error.message), events,
    classification: classify(events),
    visibleText: (await page.locator("body").innerText()).slice(-12000),
  });
});

/**
 * 每个用例一份新作品：会话与草稿都按作品保存，共用一份会让后一个用例继承前一个的
 * 对话历史——模型会按上一句要求继续改，测的就不是这一轮了。
 */
async function prepareWorkspace(request: APIRequestContext): Promise<void> {
  const root = mkdtempSync(join(tmpdir(), "hastings-model-e2e-"));
  workspaceRoot = join(root, "雪山旅馆");
  const opened = await request.post(BASE, { data: { path: workspaceRoot, create: true } });
  expect(opened.ok()).toBeTruthy();
  bindingId = (await opened.json()).id;
  writeFileSync(join(workspaceRoot, FIRST), "# 雨夜\n\n雪已经下了三天。\n", "utf-8");
  writeFileSync(join(workspaceRoot, CHAPTER), CHAPTER_TEXT, "utf-8");
  writeFileSync(join(workspaceRoot, OTHER), OTHER_TEXT, "utf-8");
  writeFileSync(join(workspaceRoot, TRUTH), TRUTH_TEXT, "utf-8");
  const recorded = await request.post(`${BASE}/${bindingId}/git/record`, {
    data: { message: "准备验收作品" },
  });
  expect(recorded.ok()).toBeTruthy();
}

async function openWorkspace(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByLabel("选择作品目录").selectOption(bindingId);
  await expect(page.getByLabel("选择作品目录")).toHaveValue(bindingId, { timeout: 10_000 });
}

/** 新建对话并发送一句要求；发送后不等待，由 waitForRun 轮询服务端。 */
async function send(page: Page, text: string): Promise<void> {
  // 每个用例都从新对话开始：不继承上一轮的历史与草稿。
  await page.getByRole("button", { name: "新对话", exact: true }).click();
  const input = page.locator(".chat-input");
  await expect(input).toBeVisible({ timeout: 10_000 });
  await input.fill(text);
  await input.press("Enter");
}

async function digestOf(request: APIRequestContext, path: string): Promise<string> {
  const response = await request.get(`${BASE}/${bindingId}/file`, { params: { path } });
  expect(response.ok(), `读取 ${path} 失败`).toBeTruthy();
  return (await response.json()).digest;
}

async function waitForRun(
  request: APIRequestContext,
  timeoutMs = 180_000,
  afterSeq = 0,
): Promise<{ conversationId: string; events: WorkspaceEvent[] }> {
  const deadline = Date.now() + timeoutMs;
  let conversationId = "";
  let events: WorkspaceEvent[] = [];
  while (Date.now() < deadline) {
    const listed = await request.get(`${BASE}/${bindingId}/conversations`);
    const items = listed.ok() ? ((await listed.json()).items ?? []) : [];
    conversationId = items[0]?.id ?? conversationId;
    if (conversationId) {
      const snapshot = await request.get(
        `${BASE}/${bindingId}/conversations/${conversationId}/messages`,
        { params: { after: afterSeq } },
      );
      events = snapshot.ok() ? ((await snapshot.json()).events ?? []) : [];
      if (events.some((event) => event.type === "run_finished")) break;
    }
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }
  if (!events.some((event) => event.type === "run_finished")) {
    throw new Error(`等待这一轮结束超时（${timeoutMs}ms）；最后收到 ${events.length} 条事件`);
  }
  return { conversationId, events };
}

/** 资料任务有自己的会话，不在写作会话列表里；按任务接口轮询。 */
async function waitForMaterialTask(
  request: APIRequestContext,
  timeoutMs = 180_000,
): Promise<{ task: Record<string, unknown> & { status?: string; conversation_id?: string }; events: WorkspaceEvent[] }> {
  const deadline = Date.now() + timeoutMs;
  let task: (Record<string, unknown> & { status?: string; conversation_id?: string }) | null = null;
  while (Date.now() < deadline) {
    const listed = await request.get(`${BASE}/${bindingId}/material-tasks?domain=chapter_index`);
    const items = listed.ok() ? ((await listed.json()).items ?? []) : [];
    task = items[0] ?? null;
    if (task && !["accepted", "running"].includes(String(task.status))) {
      const snapshot = await request.get(
        `${BASE}/${bindingId}/conversations/${task.conversation_id}/messages`,
      );
      const events = snapshot.ok() ? ((await snapshot.json()).events ?? []) : [];
      return { task, events };
    }
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }
  throw new Error(`等待资料任务结束超时；最后状态 ${task?.status ?? "没有任务"}`);
}

/** 事件只能给出初步分类，断言失败仍需结合页面和脚本判读。 */
function classify(events: WorkspaceEvent[]): string {
  const error = events.find((event) => event.type === "error");
  if (error) {
    const code = String(error.code ?? "");
    const message = String(error.message ?? "");
    if (/MODEL_|PROVIDER|CONNECT/i.test(code) || /连接|端点|凭据|key/i.test(message)) {
      return "端点或网络";
    }
    return "产品接线或规则";
  }
  const failedTool = events.find(
    (event) => event.type === "tool_finished" && event.ok === false,
  );
  if (failedTool) return "产品接线或规则";
  return "待判读（没有已记录的端点或工具错误）";
}

function saveEvidence(name: string, payload: Record<string, unknown>): void {
  const file = join(ARTIFACTS, `${name}-${Date.now()}.json`);
  writeFileSync(file, JSON.stringify(payload, null, 2), "utf-8");
}

test.describe("在线模型", () => {
  test.setTimeout(240_000);

  test("可选参考发送、范围展示与引用草稿切换", async ({ page, request }) => {
    await prepareWorkspace(request);
    const chapterBefore = await digestOf(request, CHAPTER);
    const otherBefore = await digestOf(request, OTHER);
    const truthBefore = await digestOf(request, TRUTH);
    await openWorkspace(page);
    await page.getByRole("button", { name: `加入对话 ${FIRST}`, exact: true }).click();
    await expect(page.getByLabel("本轮参考")).toContainText("雨夜");
    // 这里不点“新对话”，保留刚选中的引用快照。
    await page.locator(".chat-input").fill(SCOPE_PROMPT);
    await page.locator(".chat-input").press("Enter");
    const { conversationId, events } = await waitForRun(request);
    const after = {
      chapter: await digestOf(request, CHAPTER),
      other: await digestOf(request, OTHER),
      truth: await digestOf(request, TRUTH),
    };
    saveEvidence("scope-flow", {
      prompt: SCOPE_PROMPT,
      conversation: conversationId,
      events,
      before: { chapter: chapterBefore, other: otherBefore, truth: truthBefore },
      after,
      classification: classify(events),
    });

    const finished = events.find((event) => event.type === "run_finished") ?? {};
    expect(finished.status, `整轮状态；失败分类：${classify(events)}`).toBe("completed");
    expect(after.truth, "禁止改动的文件被改了").toBe(truthBefore);
    expect(after.other, "范围外的章节被改了").toBe(otherBefore);
    expect(after.chapter, "目标章节没有任何改动").not.toBe(chapterBefore);
    // 写入前必须登记范围：事件里要能看到本轮范围。
    expect(events.some((event) => event.type === "task_scope_set")).toBe(true);
    const turn = page.getByRole("region", { name: "一轮对话" });
    // section 的 aria-label 对应 region；断言真实渲染，不只轮询接口。
    await expect(turn.getByTestId("turn-result")).toHaveAttribute("data-status", "completed");
    const scope = turn.getByTestId("task-scope");
    await scope.locator("summary").click();
    await expect(scope).toContainText(`允许修改 ${CHAPTER}`);
    const materials = turn.getByTestId("turn-materials");
    await materials.locator("summary").click();
    await expect(materials).toContainText(FIRST);
    await expect(materials).toContainText("已提供全文");
    // 同一段文字之外，引用也必须随会话草稿隔离并恢复。
    await page.locator(".chat-input").fill("原会话的下一轮草稿");
    await page.getByRole("button", { name: `加入对话 ${FIRST}`, exact: true }).click();
    await page.getByRole("button", { name: "新对话", exact: true }).click();
    await expect(page.locator(".chat-input")).toHaveValue("");
    await page.locator(".chat-input").fill("新会话草稿");
    await page.getByLabel("选择对话").selectOption(conversationId);
    await expect(page.locator(".chat-input")).toHaveValue("原会话的下一轮草稿");
    await expect(page.getByLabel("本轮参考")).toContainText("雨夜");
  });

  test("改完检查会形成报告并与磁盘版本一致", async ({ page, request }) => {
    await prepareWorkspace(request);
    await openWorkspace(page);
    await send(page, REVIEW_PROMPT);
    const { conversationId, events } = await waitForRun(request);
    const reports = await request.get(`${BASE}/${bindingId}/reviews`);
    const items: ReviewReport[] = reports.ok() ? ((await reports.json()).items ?? []) : [];
    saveEvidence("review-flow", {
      prompt: REVIEW_PROMPT,
      conversation: conversationId,
      events,
      reports: items,
      classification: classify(events),
    });

    const review = events.find((event) => event.type === "review_finished");
    expect(review, `没有检查报告；失败分类：${classify(events)}`).toBeTruthy();
    expect(events.find((event) => event.type === "run_finished")?.status).toBe("completed");
    expect(["findings", "no_findings"]).toContain(String(review?.status));
    expect(items.length, "报告没有落到 /reviews 里").toBeGreaterThan(0);
    // 回看时的可用性由服务端按当前磁盘算：刚检查完应当是有效的。
    expect(items[0].freshness).toBe("valid");
    expect(items[0].report_id).toBe(review?.report_id);
    for (const [path, digest] of items[0].target_digests as [string, string][]) {
      await expect(digestOf(request, path)).resolves.toBe(digest);
    }
    const chapter = await request.get(`${BASE}/${bindingId}/file`, { params: { path: CHAPTER } });
    expect((await chapter.json()).text).toContain("窗外又飘起雪");
    const card = page.getByTestId("review-report");
    await expect(card).toBeVisible();
    await expect(card).toHaveAttribute("data-status", String(review?.status));
    await expect(card).toContainText(review?.status === "findings" ? "发现问题" : "没有发现问题");
    await expect(page.getByTestId("turn-result")).toHaveAttribute("data-status", "completed");
    await page.reload();
    await expect(card).toHaveAttribute("data-status", String(review?.status));
    writeFileSync(join(workspaceRoot, CHAPTER), "# 书房\n\n检查后外部改写。\n", "utf-8");
    await page.reload();
    await expect(card).toContainText("已过期");
    await expect(card).toContainText("结论不再对应当前版本");
  });

  test("故事资料整理、外部改源失效与重新读取", async ({ page, request }) => {
    await prepareWorkspace(request);
    await openWorkspace(page);
    await page.goto(`/#/workspaces/${bindingId}/materials/legacy-story`);
    await expect(page.locator(".materials-nav")).toBeVisible();
    await page.getByRole("button", { name: "整理所选章节", exact: true }).click();
    const picker = page.getByRole("dialog", { name: "整理章节索引" });
    await picker.getByRole("button", { name: "整理所选章节", exact: true }).click();
    const firstRun = await waitForMaterialTask(request);
    saveEvidence("materials-index-flow", { task: firstRun.task, events: firstRun.events });
    expect(firstRun.task.status).toBe("completed");
    expect(firstRun.events.some((event) => event.type === "material_target_published")).toBe(true);
    // 资料任务不能使用写文件工具。
    const toolNames = firstRun.events
      .filter((event) => event.type === "tool_started")
      .map((event) => String(event.tool ?? ""));
    expect(toolNames).not.toContain("write_file");
    expect(toolNames).not.toContain("edit_file");

    // 章节与事件页把章节概览与时间线合成一份投影；按章标题定位真实的一章。
    const row = page.locator("section").filter({ hasText: "第一章 雨夜" }).first();
    await expect(row).toContainText("有结果", { timeout: 30_000 });
    writeFileSync(join(workspaceRoot, FIRST), "# 雨夜\n\n雪已经停了三天。\n", "utf-8");
    await page.getByRole("button", { name: "重新读取", exact: true }).click();
    await expect(row).toContainText("依据已改动", { timeout: 30_000 });

    // 回到写作页：资料页的浏览不改变写作草稿与当前文件。
    await page.getByRole("link", { name: "写作", exact: true }).click();
    const prompt = "第一章被外部修改了。读取当前原文后，只把第三章末句改成『窗外的雪已经停了三天』。";
    await send(page, prompt);
    const next = await waitForRun(request, 180_000);
    saveEvidence("materials-index-writing", { prompt, next: next.events });
    expect(next.events.find((event) => event.type === "run_finished")?.status).toBe("completed");
    const digest = await digestOf(request, FIRST);
    expect(
      next.events.some(
        (event) =>
          event.type === "material_read" && event.path === FIRST && event.digest === digest,
      ),
    ).toBe(true);
    const changed = await request.get(`${BASE}/${bindingId}/file`, { params: { path: CHAPTER } });
    expect((await changed.json()).text).toContain("窗外的雪已经停了三天");
    await expect(page.getByTestId("turn-result").last()).toHaveAttribute("data-status", "completed");
    await expect(page.getByTestId("task-scope").last()).toBeVisible();

    const materials = await request.get(`${BASE}/${bindingId}/story-materials/story_index`);
    const entry = (await materials.json()).entries.find(
      (item: { chapter: number }) => item.chapter === 1,
    );
    expect(entry.coverage).toBe("found");
    expect(entry.freshness).toBe("stale");
  });
});
