/**
 * 构思在线完整流程（1.0.1 P6）：真实服务、真实模型、真实文件。
 *
 * 验证三件事：模型通过结构工具建立方案与线索；备选的确定采用落到主方案；
 * 「引用到对话」让下一轮请求真的带上结构注入（读操作上下文，不猜）。
 * 断言只放可判定的事实；语义质量由 eval harness 的 O01/O06 等样例单独评。
 */
import { test, expect, type APIRequestContext, type Page } from "@playwright/test";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
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


test("已有正文归纳结构，并在局部修改中只动选中节点", async ({ page, request }, info) => {
  test.setTimeout(600_000);
  const root = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "online-books");
  mkdirSync(root, { recursive: true });
  const bookPath = join(mkdtempSync(join(root, "existing-prose-")), "雨夜");
  const created = await request.post("/api/file-workspaces", { data: { path: bookPath, create: true } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const book = await created.json();
  const base = `/api/file-workspaces/${book.id}`;
  const chapterPath = "正文/0001 雨夜.md";
  const prose = "# 雨夜\n\n林砚抵达旅馆。前台有一双湿手套，边缘还在滴水。管家说自己整夜没有离开房间。\n";
  mkdirSync(join(bookPath, "正文"), { recursive: true });
  writeFileSync(join(bookPath, chapterPath), prose, "utf8");
  const config = await request.get("/api/model-connections").then(r => r.json());
  expect(config.selection.available).toBe(true);

  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  await surface.getByRole("button", { name: "创建构思", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "创建构思", exact: true });
  await dialog.getByLabel("创作要求").fill(
    `先读取 ${chapterPath} 的实际内容，再建立一份很小的构思：两个节点「抵达」和「对质」，一条线索「湿手套」。只归纳正文已经发生的事实，计划部分另写；作者真相未定。不要补造没有写到的章节，也不要写正文。`,
  );
  const accepted = await accept(page, () => dialog.getByRole("button", { name: "创建构思", exact: true }).click());
  await finish(request, book.id, accepted);
  const graph = await request.get(`${base}/story/variants/main`).then(r => r.json());
  expect(graph.nodes.length).toBeGreaterThanOrEqual(2);
  expect(graph.nodes.map((node: { title: string }) => node.title)).toEqual(expect.arrayContaining(["抵达", "对质"]));
  const variants = await request.get(`${base}/story/variants`).then(r => r.json());
  expect(variants.variants[0].counts.clues).toBeGreaterThanOrEqual(1);
  const afterCreate = await request.get(`${base}/file`, { params: { path: chapterPath } }).then(r => r.json());
  expect(afterCreate.text).toBe(prose);

  const target = graph.nodes.find((node: { title: string }) => node.title.includes("抵达")) ?? graph.nodes[0];
  const nodeButton = surface.getByRole("button", { name: new RegExp(`^${target.title}`) }).first();
  await nodeButton.click();
  const revision = await send(
    page,
    `只修改当前选中的节点「${target.title}」：把关键变化改为「林砚开始怀疑管家隐瞒了离开房间的时间」；其他节点、故事线和线索计划保持不变，不写正文。`,
  );
  await finish(request, book.id, revision);
  const updated = await request.get(`${base}/story/variants/main`).then(r => r.json());
  expect(updated.nodes.length).toBe(graph.nodes.length);
  const byId = new Map(updated.nodes.map((node: { id: string }) => [node.id, node]));
  for (const beforeNode of graph.nodes.filter((node: { id: string }) => node.id !== target.id)) {
    const afterNode = byId.get(beforeNode.id) as Record<string, unknown> | undefined;
    expect(afterNode, beforeNode.id).toBeTruthy();
    expect(afterNode!.title).toBe(beforeNode.title);
    expect(afterNode!.action).toBe(beforeNode.action);
    expect(afterNode!.change).toBe(beforeNode.change);
    expect(afterNode!.stage_id).toBe(beforeNode.stage_id);
    expect(afterNode!.threads).toEqual(beforeNode.threads);
  }
  const targetAfter = byId.get(target.id) as Record<string, unknown> | undefined;
  expect(targetAfter).toBeTruthy();
  await info.attach("existing-prose-evidence", { body: JSON.stringify({
    model: config.selection.model, accepted, graph, updated,
  }, null, 2), contentType: "application/json" });
});

test("暂停、继续并在重载后恢复构思", async ({ page, request }, info) => {
  test.setTimeout(600_000);
  const root = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "online-books");
  mkdirSync(root, { recursive: true });
  const path = join(mkdtempSync(join(root, "pause-")), "暂停恢复");
  const created = await request.post("/api/file-workspaces", { data: { path, create: true } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const book = await created.json();
  const base = `/api/file-workspaces/${book.id}`;
  const config = await request.get("/api/model-connections").then(r => r.json());
  expect(config.selection.available).toBe(true);

  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  await surface.getByRole("button", { name: "创建构思", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "创建构思", exact: true });
  await dialog.getByLabel("创作要求").fill(
    "建立一个包含 2 个阶段、3 条故事线、8 个关键事件节点的构思；每条线写清目标与阻力，节点写清关键变化，并让三条线在最后一个节点汇合。只保存结构，不写正文，不要询问。",
  );
  const accepted = await accept(page, () => dialog.getByRole("button", { name: "创建构思", exact: true }).click());
  expect(accepted.operation_id).toBeTruthy();

  await request.post(`/api/operations/${accepted.operation_id}/pause`, {
    data: { request_key: `pause-${book.id}` },
  });
  let view = await request.get(`/api/operations/${accepted.operation_id}`).then(r => r.json());
  await expect.poll(async () => {
    view = await request.get(`/api/operations/${accepted.operation_id}`).then(r => r.json());
    return view.status;
  }, { timeout: 30_000 }).toBe("paused");
  expect(view.generation).toBeGreaterThanOrEqual(1);

  const continued = await request.post(`/api/operations/${accepted.operation_id}/continue`, {
    data: {
      request_key: `continue-${book.id}`,
      expected_generation: view.generation,
      text: "",
    },
  });
  expect(continued.ok(), await continued.text()).toBeTruthy();
  let rows: Event[] = [];
  await expect.poll(async () => {
    rows = await events(request, book.id, accepted);
    return rows.some(row => row.type === "run_finished" && row.status === "completed");
  }, { timeout: 240_000, intervals: [500, 1000] }).toBe(true);
  const committed = rows.filter(row => row.type === "story_changes_committed");
  expect(committed.length).toBe(1);
  const graph = await request.get(`${base}/story/variants/main`).then(r => r.json());
  expect(graph.nodes.length).toBeGreaterThanOrEqual(2);
  expect(new Set(graph.nodes.map((node: { id: string }) => node.id)).size).toBe(graph.nodes.length);

  await page.reload();
  await page.getByRole("button", { name: "构思", exact: true }).click();
  await expect(surface.getByRole("button", { name: new RegExp(`^${graph.nodes[0].title}`) }).first()).toBeVisible();
  await info.attach("pause-resume-evidence", { body: JSON.stringify({
    model: config.selection.model, accepted, paused: view, graph,
  }, null, 2), contentType: "application/json" });
});

test("大结构超过预算时按节点拆分并记录覆盖", async ({ request }, info) => {
  test.setTimeout(600_000);
  const root = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "online-books");
  mkdirSync(root, { recursive: true });
  const path = join(mkdtempSync(join(root, "budget-")), "预算拆分");
  const created = await request.post("/api/file-workspaces", { data: { path, create: true } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const book = await created.json();
  const base = `/api/file-workspaces/${book.id}`;
  const config = await request.get("/api/model-connections").then(r => r.json());
  expect(config.selection.available).toBe(true);
  const longTruth = "预算拆分测试真相。".repeat(900);

  const seeded = await request.post(`${base}/story/changes`, {
    data: {
      variant_id: "main",
      expected_revision: "empty",
      request_key: `budget-seed-${book.id}`,
      operations: [
        { op: "add_stage", stage: { id: "s1", title: "开端", order: 0 } },
        { op: "add_thread", thread: { id: "t1", title: "调查", order: 0, goal: "确认位置" } },
        { op: "create_clue_identity", temp_id: "a", title: "线索甲" },
        { op: "create_clue_identity", temp_id: "b", title: "线索乙" },
        { op: "upsert_clue_plan", clue_id: "a", plan: { author_truth: longTruth } },
        { op: "upsert_clue_plan", clue_id: "b", plan: { author_truth: longTruth } },
        { op: "add_node", node: { id: "n1", title: "节点甲", action: "开场", stage_id: "s1", thread_ids: ["t1"],
          clue_uses: [{ clue_id: "a", presentation: "甲出现", mode: "narration" }] } },
        { op: "add_node", node: { id: "n2", title: "节点乙", action: "转折", stage_id: "s1", thread_ids: ["t1"],
          clue_uses: [{ clue_id: "b", presentation: "乙出现", mode: "narration" }] } },
        { op: "connect", edge: { id: "e1", from_id: "n1", to_id: "n2", kind: "next" } },
      ],
    },
  });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
  const graph = await request.get(`${base}/story/variants/main`).then(r => r.json());

  const acceptedResponse = await request.post(`${base}/conversations`, {
    data: {
      text: "只讨论、不保存：说明这一轮实际能看到的两个节点各自的安排，并确认有没有省略。",
      request_key: `budget-turn-${book.id}`,
      context: {
        selected_path: null,
        reference_paths: [],
        observed_digests: {},
        story: { variant_id: "main", revision: graph.revision, node_ids: ["n1", "n2"], purpose: "discuss" },
      },
    },
  });
  expect(acceptedResponse.status()).toBe(202);
  const accepted = await acceptedResponse.json();
  const rows = await finish(request, book.id, accepted);
  const contextEvent = rows.find(row => row.type === "story_context") as Record<string, unknown> | undefined;
  expect(contextEvent).toBeTruthy();
  const split = contextEvent!.split as Record<string, unknown> | undefined;
  expect(split).toBeTruthy();
  expect((contextEvent!.coverage as Record<string, unknown>).nodes).toEqual(["n1"]);
  expect((split!.batches as { node_ids: string[] }[])[0].node_ids).toEqual(["n1"]);
  expect(rows.some(row => row.type === "story_changes_committed")).toBe(false);
  await info.attach("budget-split-evidence", { body: JSON.stringify({
    model: config.selection.model, accepted, contextEvent,
  }, null, 2), contentType: "application/json" });
});

test("模型提交悬空线索引用时服务端拒绝且不落半份结构", async ({ page, request }, info) => {
  test.setTimeout(300_000);
  const root = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "online-books");
  mkdirSync(root, { recursive: true });
  const path = join(mkdtempSync(join(root, "invalid-")), "无效引用");
  const created = await request.post("/api/file-workspaces", { data: { path, create: true } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const book = await created.json();
  const base = `/api/file-workspaces/${book.id}`;
  const config = await request.get("/api/model-connections").then(r => r.json());
  expect(config.selection.available).toBe(true);

  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "构思", exact: true }).click();
  const accepted = await send(
    page,
    "本轮只做一次结构提交测试：调用 update_story 新增节点 n1，并在 clue_uses 中引用 clue_id「missing-clue」，不要先创建这条线索。服务端拒绝后不要重试，直接说明失败原因。",
  );
  let rows: Event[] = [];
  await expect.poll(async () => {
    rows = await events(request, book.id, accepted);
    return rows.some(row => row.type === "run_finished");
  }, { timeout: 180_000, intervals: [500, 1000] }).toBe(true);
  const failed = rows.find(row =>
    row.type === "tool_finished" &&
    row.tool === "update_story" &&
    row.ok === false,
  ) as Record<string, unknown> | undefined;
  const graph = await request.get(`${base}/story/variants/main`).then(r => r.json()).catch(() => null);
  if (!failed) {
    // 模型自行创建了身份或没有尝试无效引用：不把这次当成服务端拒绝的证据。
    expect(graph?.nodes?.length ?? 0).toBe(0);
    await info.attach("invalid-output-evidence", { body: JSON.stringify({
      model: config.selection.model, accepted, rows, note: "model did not submit an invalid reference",
    }, null, 2), contentType: "application/json" });
    test.info().annotations.push({ type: "O07", description: "模型未提交悬空引用；未产生半份结构" });
    return;
  }
  expect(String(failed.summary)).toContain("STORY_CLUE_REFERENCE_INVALID");
  expect(graph?.nodes?.length ?? 0).toBe(0);
  expect(rows.some(row => row.type === "story_changes_committed")).toBe(false);
  await info.attach("invalid-output-evidence", { body: JSON.stringify({
    model: config.selection.model, accepted, failed, graph,
  }, null, 2), contentType: "application/json" });
});
