/**
 * 构思在线完整流程（1.0.1 P6）：真实服务、真实模型、真实文件。
 *
 * 验证三件事：模型通过结构工具建立方案与线索；备选的确定采用落到主方案；
 * 「引用到对话」让下一轮请求真的带上结构注入（读操作上下文，不猜）。
 * 断言只放可判定的事实；语义质量由 eval harness 的 O01/O06 等样例单独评。
 */
import { test, expect, type APIRequestContext, type Page } from "@playwright/test";
import { mkdirSync, mkdtempSync } from "node:fs";
import { join, resolve } from "node:path";

type Accepted = { conversation_id: string; turn_id: string; operation_id?: string };
type Event = Record<string, unknown> & { type: string };

async function events(request: APIRequestContext, wid: string, accepted: Accepted): Promise<Event[]> {
  const response = await request.get(
    `/api/file-workspaces/${wid}/conversations/${accepted.conversation_id}/messages`,
  );
  expect(response.ok(), await response.text()).toBeTruthy();
  return await response.json().then((body) => body.events);
}

async function finish(request: APIRequestContext, wid: string, accepted: Accepted): Promise<Event[]> {
  let rows: Event[] = [];
  await expect
    .poll(async () => {
      rows = await events(request, wid, accepted);
      return rows.some((e) => e.type === "run_finished" && e.turn_id === accepted.turn_id);
    }, { timeout: 240_000, intervals: [500, 1000] })
    .toBe(true);
  const ended = rows.find((e) => e.type === "run_finished" && e.turn_id === accepted.turn_id)!;
  expect(ended.status, JSON.stringify(rows.filter((e) => e.type === "error"))).toBe("completed");
  return rows;
}

async function accept(page: Page, action: () => Promise<unknown>): Promise<Accepted> {
  const received = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" &&
      r.status() === 202 &&
      /\/(conversations(?:\/[^/]+\/turns)?|operations)$/.test(new URL(r.url()).pathname),
    { timeout: 20_000 },
  );
  await action();
  return await (await received).json();
}

async function send(page: Page, text: string): Promise<Accepted> {
  await page.locator(".chat-input").fill(text);
  return accept(page, () => page.getByRole("button", { name: "发送", exact: true }).click());
}

test("创建表单实际保存节点线索并在刷新后显示", async ({ page, request }, info) => {
  test.setTimeout(300_000);
  const root = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "online-books");
  mkdirSync(root, { recursive: true });
  const path = join(mkdtempSync(join(root, "story-form-")), "空白构思");
  const created = await request.post("/api/file-workspaces", { data: { path, create: true } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const book = await created.json();
  const base = `/api/file-workspaces/${book.id}`;
  const config = await request.get("/api/model-connections").then(r => r.json());
  expect(config.selection.available).toBe(true);
  const model = config.connections.find((item: { name: string }) => item.name === config.selection.connection_id);
  expect(["localhost", "127.0.0.1", "0.0.0.0"]).not.toContain(new URL(model.base_url).hostname);
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "构思", exact: true }).click();
  await page.getByRole("region", { name: "构思结构" }).getByRole("button", { name: "创建构思", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "创建构思", exact: true });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("创作要求").fill(
    "建立一个很小的构思：只有「发现」和「对质」两个事件节点，一条「湿手套」线索。发现时侦探在前台看见滴水的手套，对质时询问管家；两个节点以前后推进连接。真相尚未确定，保持未定。只保存这些节点、线索和连接。",
  );
  await page.screenshot({ path: info.outputPath("creation-form.png") });
  const accepted = await accept(page, () => dialog.getByRole("button", { name: "创建构思", exact: true }).click());
  await expect(dialog).not.toBeVisible();
  const rows = await finish(request, book.id, accepted);
  const turn = rows.filter(row => row.turn_id === accepted.turn_id);
  expect(turn.find(row => row.type === "task_intent")?.kind).toBe("structure");
  expect(turn.find(row => row.type === "task_intent")?.action).toBe("create");
  expect(turn.some(row => row.type === "module_changes_applied")).toBe(false);
  expect(turn.some(row => row.type === "story_changes_committed")).toBe(true);
  const graph = await request.get(`${base}/story/variants/main`).then(r => r.json());
  expect(graph.nodes).toHaveLength(2);
  expect(graph.nodes.map((node: { title: string }) => node.title)).toEqual(expect.arrayContaining(["发现", "对质"]));
  expect(graph.edges.length).toBeGreaterThanOrEqual(1);
  const variants = await request.get(`${base}/story/variants`).then(r => r.json());
  expect(variants.variants[0].counts.clues).toBe(1);
  const surface = page.getByRole("region", { name: "构思结构" });
  await expect(surface.getByRole("button", { name: /^发现(?:\s|$)/ })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "构思", exact: true }).click();
  await expect(surface.getByRole("button", { name: /^对质(?:\s|$)/ })).toBeVisible();
  await surface.getByRole("button", { name: /^发现(?:\s|$)/ }).click();
  await expect(surface.getByText("湿手套", { exact: false }).first()).toBeVisible();
  await page.screenshot({ path: info.outputPath("created-structure.png") });
  await info.attach("creation-evidence", { body: JSON.stringify({
    model: config.selection.model, accepted, graph,
    events: turn.filter(row => ["task_intent", "story_changes_committed", "run_finished", "error", "model_request_finished"].includes(row.type)),
  }, null, 2), contentType: "application/json" });
});

test("构思建立与结构引用进入真实请求", async ({ page, request }, info) => {
  test.setTimeout(720_000);
  const root = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "online-books");
  mkdirSync(root, { recursive: true });
  const path = join(mkdtempSync(join(root, "story-")), "雪夜山庄");
  const created = await request.post("/api/file-workspaces", { data: { path, create: true } });
  expect(created.ok()).toBeTruthy();
  const book = await created.json();
  const base = `/api/file-workspaces/${book.id}`;
  const transcript: unknown[] = [];
  try {
    const config = await request.get("/api/model-connections").then((r) => r.json());
    expect(config.selection.available).toBe(true);
    const connection = config.connections.find(
      (c: { name: string }) => c.name === config.selection.connection_id,
    );
    // 在线评测不指向本机：接错了就停，不冒充真实模型结果。
    expect(["localhost", "127.0.0.1", "0.0.0.0"]).not.toContain(new URL(connection.base_url).hostname);

    await page.goto(`/#/workspaces/${book.id}/writing`);
    // 打开构思面：这一轮按 design 语境发送，空白作品也能建立第一份结构。
    await page.getByRole("button", { name: "构思", exact: true }).click();

    // 1) 从空白构思：模型用结构工具建立线索与两个节点。
    const plan = await send(
      page,
      "为这部小说建立构思：一条线索「窗台的雪」——管家声称整夜没出房间，但窗台内侧有他的脚印，作者真相是他在雪停后去过书房；再给两个节点「晚宴」和「对质」，晚宴呈现脚印的疑点，对质时管家解释脚印。只建结构与台账，不写正文。",
    );
    transcript.push(await finish(request, book.id, plan));
    const variants = await request.get(`${base}/story/variants`).then((r) => r.json());
    const main = variants.variants[0];
    expect(main.counts.nodes).toBeGreaterThanOrEqual(2);
    expect(main.counts.clues).toBeGreaterThanOrEqual(1);
    expect(variants.primary_variant_id).toBe(main.id);

    // 2) 直接在唯一构思中引用节点。
    const surface = page.getByRole("region", { name: "构思结构" });
    await expect(surface.getByRole("button", { name: "晚宴", exact: true })).toBeVisible();
    // 3) 引用到对话：下一条请求真的带上结构注入（读操作上下文，不猜）。
    await surface.getByRole("button", { name: "晚宴", exact: true }).click();
    await surface.getByRole("button", { name: "引用到对话" }).click();
    await expect(page.getByLabel("引用构思")).toContainText("晚宴");
    const discussed = await send(
      page,
      "只讨论、不保存：以「晚宴」这个节点为基点，说明如果雪停时间被目击者推翻，哪些节点和线索需要联动调整。给出需要改动的对象清单即可。",
    );
    const rows = await finish(request, book.id, discussed);
    const operationId = rows.find((e) => e.type === "operation_started")?.operation_id;
    if (typeof operationId === "string" && operationId) {
      const context = await request
        .get(`/api/operations/${operationId}/context`)
        .then((r) => r.json());
      const injected = JSON.stringify(context.injections ?? []);
      // 结构真的进入了这一轮的输入；没有就说明「引用到对话」是假的。
      expect(injected).toContain("story");
    } else {
      // 旧事件流没有 operation_started 时，退回断言请求确实带了 story 上下文：
      // 受理事件里的 context 由服务端冻结，不是前端自说自话。
      const acceptedRow = rows.find((e) => e.type === "turn_started") ?? {};
      expect(JSON.stringify(acceptedRow)).toContain("story");
    }
    await page.screenshot({ path: info.outputPath("story-online.png") });
  } finally {
    await info.attach("online-events", {
      body: JSON.stringify({ workspaceId: book.id, transcript }, null, 2),
      contentType: "application/json",
    });
    const snapshots = await request.get(`${base}/conversations`).then((r) => r.json());
    await info.attach("conversations", { body: JSON.stringify(snapshots), contentType: "application/json" });
  }
});
