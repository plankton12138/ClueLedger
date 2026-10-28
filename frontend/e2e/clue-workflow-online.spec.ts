/** 在线完整流程：真实服务、真实模型、真实文件；不预置线索或报告。 */
import { test, expect, type APIRequestContext, type Page } from "@playwright/test";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const FIRST = "正文/0001 遗书.md";
type Accepted = { conversation_id: string; turn_id: string };
type Event = Record<string, unknown> & { type: string };

async function events(request: APIRequestContext, wid: string, accepted: Accepted): Promise<Event[]> {
  const response = await request.get(`/api/file-workspaces/${wid}/conversations/${accepted.conversation_id}/messages`);
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()).events;
}
async function finish(request: APIRequestContext, wid: string, accepted: Accepted) {
  let rows: Event[] = [];
  await expect.poll(async () => {
    rows = await events(request, wid, accepted);
    return rows.some(e => e.type === "run_finished" && e.turn_id === accepted.turn_id);
  }, { timeout: 180_000, intervals: [500, 1000] }).toBe(true);
  const ended = rows.find(e => e.type === "run_finished" && e.turn_id === accepted.turn_id)!;
  expect(ended.status, JSON.stringify(rows.filter(e => e.type === "error"))).toBe("completed");
  return rows;
}
async function accept(page: Page, action: () => Promise<unknown>): Promise<Accepted> {
  const received = page.waitForResponse(r => r.request().method() === "POST" && r.status() === 202 &&
    /\/(conversations(?:\/[^/]+\/turns)?|material-tasks)$/.test(new URL(r.url()).pathname), { timeout: 20_000 });
  await action();
  return (await received).json();
}
async function send(page: Page, text: string) {
  await page.locator(".chat-input").fill(text);
  return accept(page, () => page.getByRole("button", { name: "发送", exact: true }).click());
}

test("保存计划、整理、核对、引用改写、复查与停止均落到在线请求", async ({ page, request }, info) => {
  test.setTimeout(720_000);
  const root = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "online-books");
  mkdirSync(root, { recursive: true });
  const path = join(mkdtempSync(join(root, "clue-")), "遗书验收");
  const created = await request.post("/api/file-workspaces", { data: { path, create: true } });
  expect(created.ok()).toBeTruthy();
  const book = await created.json();
  writeFileSync(join(book.path, FIRST), "# 遗书\n\n林岚没有拆开信封，却说出了信背面的句子「青鸟归巢」。侦探记下了她的说法。\n", "utf8");
  const base = `/api/file-workspaces/${book.id}`;
  const transcript: unknown[] = [];
  try {
    const config = await request.get("/api/model-connections").then(r => r.json());
    expect(config.selection.available).toBe(true);
    const connection = config.connections.find((c: { name: string }) => c.name === config.selection.connection_id);
    expect(["localhost", "127.0.0.1", "0.0.0.0"]).not.toContain(new URL(connection.base_url).hostname);
    await page.goto(`/#/workspaces/${book.id}/writing`);
    await page.getByRole("button", { name: "第一章 遗书", exact: true }).click();
    const plan = await send(page, "创建并装入当前作品一个线索模块，标题叫「遗书构思」，保存一条「照片解释」：林岚可能从公开照片知道遗书背面的句子，需在第二章验证。只保存这一条构思，不改正文，不认定林岚提前接触过原件。");
    transcript.push(await finish(request, book.id, plan));
    const assembly = await request.get(`${base}/assembly`).then(r => r.json());
    expect(assembly.bindings.length).toBeGreaterThan(0);
    await page.getByRole("button", { name: "本章线索", exact: true }).click();
    const organize = await accept(page, () => page.getByRole("button", { name: "整理本章", exact: true }).click());
    transcript.push(await finish(request, book.id, organize));
    const list = await request.get(`${base}/clues`, { params: { chapter_path: FIRST, as_of_chapter: 1 } }).then(r => r.json());
    expect(list.items.length).toBeGreaterThan(0);
    const clue = list.items[0];
    const pane = page.getByRole("tabpanel", { name: "本章线索" });
    await pane.getByRole("button").filter({ hasText: clue.title }).first().click();
    const detail = pane.getByRole("region", { name: "线索详情" });
    await detail.getByRole("button", { name: "新建推理", exact: true }).click();
    const editor = page.getByRole("dialog", { name: "新建推理目标", exact: true });
    await editor.getByLabel("目标结论").fill("林岚提前接触过遗书原件");
    await editor.getByLabel("其他解释（每行一条）").fill("林岚从公开照片知道内容");
    await editor.getByRole("button", { name: "保存目标", exact: true }).click();
    const review = await accept(page, () => detail.getByRole("button", { name: "核对推理", exact: true }).click());
    transcript.push(await finish(request, book.id, review));
    await page.getByRole("button", { name: "本章线索", exact: true }).click();
    await detail.getByRole("button", { name: "查看核对报告", exact: true }).click();
    await expect(detail.getByRole("region", { name: "核对报告" })).toContainText("发现问题");
    let current = await request.get(`${base}/clues/${clue.clue_id}?as_of_chapter=1`).then(r => r.json());
    const reportId = current.reviews[0].report_id;
    await detail.getByRole("button", { name: "带入目标", exact: true }).click();
    await expect(page.locator('[aria-label="引用线索"]')).toContainText(clue.title);
    const rewrite = await send(page, "讨论后的明确修改：只在第一章末尾追加一句「林岚说出青鸟后，又改口说自己记错了。」其余正文和模块均不改，不宣布结论。");
    transcript.push(await finish(request, book.id, rewrite));
    const changed = await request.get(`${base}/file`, { params: { path: FIRST } }).then(r => r.json());
    expect(changed.text).toContain("改口");
    const old = await request.get(`${base}/clue-reviews/${reportId}`).then(r => r.json());
    expect(old.freshness).toBe("stale");
    await page.getByRole("button", { name: "本章线索", exact: true }).click();
    const recheck = await accept(page, () => detail.getByRole("button", { name: "核对推理", exact: true }).click());
    transcript.push(await finish(request, book.id, recheck));
    current = await request.get(`${base}/clues/${clue.clue_id}?as_of_chapter=1`).then(r => r.json());
    expect(current.reviews.length).toBeGreaterThanOrEqual(2);
    expect(current.reviews[0].report_id).not.toBe(reportId);
    expect(current.reviews[0].freshness).toBe("valid");
    const stopping = await send(page, "只讨论、不保存：详细列出三十种遗书线索的其他解释，每种至少写五句话，逐项展开。");
    await expect.poll(async () => (await events(request, book.id, stopping)).some(e => e.type === "model_request_started" && e.turn_id === stopping.turn_id), { timeout: 20_000 }).toBe(true);
    await page.getByRole("button", { name: "停止", exact: true }).click();
    await expect.poll(async () => (await events(request, book.id, stopping)).find(e => e.type === "run_finished" && e.turn_id === stopping.turn_id)?.status, { timeout: 20_000 }).toBe("stopped");
    const stopped = await events(request, book.id, stopping);
    const calls = stopped.filter(e => e.type === "model_request_started").length;
    await page.reload();
    expect((await events(request, book.id, stopping)).filter(e => e.type === "model_request_started").length).toBe(calls);
    transcript.push(stopped);
    await page.screenshot({ path: info.outputPath("online-workflow.png") });
  } finally {
    await info.attach("online-events", { body: JSON.stringify({ workspaceId: book.id, transcript }, null, 2), contentType: "application/json" });
    const snapshots = await request.get(`${base}/conversations`).then(r => r.json());
    await info.attach("conversations", { body: JSON.stringify(snapshots), contentType: "application/json" });
  }
});
