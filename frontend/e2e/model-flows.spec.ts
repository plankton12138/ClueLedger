import { test, expect, type APIRequestContext, type Page } from "@playwright/test";
import { mkdtempSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/**
 * 在线模型浏览器验收（0.6 V06-D2）。
 *
 * 与 `workspace.spec.ts` 的区别：这里真的调用模型，检查「作者说话 → 模型登记范围 →
 * 写入 → 检查」在浏览器里是否接通。断言只放在可判定的事实上：哪些文件变了、禁止文件
 * 的指纹是否没变、事件里有没有范围登记与检查报告。文字好不好不在自动判断范围内，
 * 由作者看样例。
 *
 * 失败分类：端点/网络、产品接线或规则、模型理解。首次失败直接留证，不重试；
 * 分类只用来判断下一步查哪里，不改变失败本身。
 */

const PORT = process.env.HASTINGS_E2E_PORT ?? "8931";
const BASE = `http://127.0.0.1:${PORT}/api/file-workspaces`;
const ARTIFACTS = join(process.cwd(), "..", "data", "test-tmp", "e2e-model");

const CHAPTER = "正文/0003 书房.md";
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

type WorkspaceEvent = Record<string, unknown> & { type?: string };

test.beforeAll(() => {
  mkdirSync(ARTIFACTS, { recursive: true });
});

/**
 * 每个用例一份新作品：会话与草稿都按作品保存，共用一份会让后一个用例继承前一个的
 * 对话历史——模型会按上一句要求继续改，测的就不是这一轮了。
 */
async function prepareWorkspace(request: APIRequestContext): Promise<void> {
  const root = mkdtempSync(join(tmpdir(), "hastings-model-e2e-"));
  const workspaceRoot = join(root, "雪山旅馆");
  const opened = await request.post(BASE, { data: { path: workspaceRoot, create: true } });
  expect(opened.ok()).toBeTruthy();
  bindingId = (await opened.json()).id;
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

/** 失败原因分三类：端点或网络、产品接线或规则、模型理解。 */
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
  return "模型理解";
}

function saveEvidence(name: string, payload: Record<string, unknown>): void {
  const file = join(ARTIFACTS, `${name}-${Date.now()}.json`);
  writeFileSync(file, JSON.stringify(payload, null, 2), "utf-8");
}

test.describe("在线模型", () => {
  test.setTimeout(240_000);

  test("只改指定章节，禁止文件不被改动", async ({ page, request }) => {
    await prepareWorkspace(request);
    const chapterBefore = await digestOf(request, CHAPTER);
    const otherBefore = await digestOf(request, OTHER);
    const truthBefore = await digestOf(request, TRUTH);
    await openWorkspace(page);
    await send(page, SCOPE_PROMPT);
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
  });

  test("改完检查会形成报告并与磁盘版本一致", async ({ page, request }) => {
    await prepareWorkspace(request);
    await openWorkspace(page);
    await send(page, REVIEW_PROMPT);
    const { conversationId, events } = await waitForRun(request);
    const reports = await request.get(`${BASE}/${bindingId}/reviews`);
    const items = reports.ok() ? ((await reports.json()).items ?? []) : [];
    saveEvidence("review-flow", {
      prompt: REVIEW_PROMPT,
      conversation: conversationId,
      events,
      reports: items,
      classification: classify(events),
    });

    const review = events.find((event) => event.type === "review_finished");
    expect(review, `没有检查报告；失败分类：${classify(events)}`).toBeTruthy();
    expect(["findings", "no_findings", "incomplete"]).toContain(String(review?.status));
    expect(items.length, "报告没有落到 /reviews 里").toBeGreaterThan(0);
    // 回看时的可用性由服务端按当前磁盘算：刚检查完应当是有效的。
    expect(items[0].freshness).toBe("valid");
    expect(items[0].report_id).toBe(review?.report_id);
    for (const path of items[0].target_paths as string[]) {
      await expect(digestOf(request, path)).resolves.toBeTruthy();
    }
  });
});
