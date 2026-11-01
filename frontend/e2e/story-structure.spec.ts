import { test, expect } from "./fixtures/frontendExperience";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

/**
 * 构思面：真实服务响应驱动，不 mock 结构接口。
 *
 * 悬疑时间轴是唯一的结构展示方式。断言只放可判定的事实：节点标题来自服务端、
 * 时间轴节点用于过滤左栏线索、窄屏与大字号码下不横向溢出。
 */

const NODES = [
  { op: "create_clue_identity", temp_id: "gloves", title: "手套" },
  {
    op: "upsert_clue_plan",
    clue_id: "gloves",
    plan: { author_truth: "管家在雪地里搬过尸体", resolution_intent: "undecided" },
  },
  { op: "create_clue_identity", temp_id: "key", title: "钥匙" },
  {
    op: "upsert_clue_plan",
    clue_id: "key",
    plan: { resolution_intent: "explain" },
  },
  {
    op: "add_node",
    node: {
      id: "n1",
      title: "抵达",
      action: "侦探在前台看到一双手套",
      clue_uses: [
        { clue_id: "gloves", presentation: "手套还在滴水", mode: "narration" },
      ],
    },
  },
  {
    op: "add_node",
    node: {
      id: "n2",
      title: "对质",
      action: "他掏出钥匙放在桌上",
      clue_uses: [{ clue_id: "key", roles: ["resolve"], presentation: "钥匙上没有雪", mode: "narration" }],
    },
  },
  {
    op: "connect",
    edge: { id: "e1", from_id: "n1", to_id: "n2", kind: "next" },
  },
];

test("无模块来源的草稿约定可由作者确认，不自动调用模型", async ({ page, book }) => {
  const state = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "state");
  execFileSync("uv", ["run", "--directory", "..", "python", resolve("e2e/fixtures/story_draft_review.py"), state, book.id], { windowsHide: true });
  await page.route("**/api/operations/op-review/continue", route => route.fulfill({
    status: 409, contentType: "application/json", body: JSON.stringify({ error: { code: "TEST_STOP", message: "测试在作者确认后停止" } }),
  }));
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("link", { name: "构思", exact: true }).click();
  await expect(page.locator(".story-draft")).toContainText("至少六名嫌疑人");
  const response = page.waitForResponse(r => r.url().endsWith("/confirm-constraints"));
  await page.getByRole("button", { name: "确认这些必需约定并继续" }).click();
  const confirmed = await response;
  expect(confirmed.ok(), await confirmed.text()).toBeTruthy();
  expect((await confirmed.json()).confirmed).toBe(1);
});

for (const width of [1440, 390]) {
  test(`空白构思在 ${width} 宽度下打开创建表单，提交失败保留要求与原草稿`, async ({ page, book }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/api/model-connections", route => route.fulfill({ json: {
      revision: "story-start-model", connections: [],
      selection: { connection_id: "test", model: "test-model", source: "saved",
        available: true, reason: "", thinking_level: null, thinking_levels: [] },
    } }));
    const submissions: { text: string; story_action?: string; context: { story?: unknown } }[] = [];
    await page.route(`**/api/file-workspaces/${book.id}/conversations`, async route => {
      if (route.request().method() !== "POST") return route.fallback();
      submissions.push(route.request().postDataJSON());
      // 只核对真实前端生成的请求，不把隔离测试发送给模型。
      await route.fulfill({ status: 422, json: { error: {
        code: "TEST_REQUEST_CAPTURED", where: "test.story_start", why: "隔离受理失败",
        fix: "保留要求后重试",
      } } });
    });

    await page.goto(`/#/workspaces/${book.id}/writing`);
    if (!(await page.locator(".chat-input").isVisible())) await page.getByRole("button", { name: "对话", exact: true }).click();
    await page.locator(".chat-input").fill("普通对话还没发送的草稿");
    if (width < 600) await page.getByRole("button", { name: "对话", exact: true }).click();
    await page.getByRole("link", { name: "构思", exact: true }).click();
    const surface = page.getByRole("region", { name: "构思结构" });
    const start = surface.getByRole("button", { name: "创建构思", exact: true });
    await expect(start).toBeVisible();
    await expect(surface.getByRole("tablist", { name: "方案" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "构思", exact: true })).toHaveAttribute("aria-current", "page");
    await start.click();
    const dialog = page.getByRole("dialog", { name: "创建构思", exact: true });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel("创作要求")).toBeFocused();
    expect(submissions).toHaveLength(0);
    await dialog.getByLabel("创作要求").fill("根据人物设定模块，建立湿手套的发现与对质。");
    await dialog.getByRole("button", { name: "创建构思", exact: true }).click();
    await expect.poll(() => submissions.length).toBe(1);
    expect(submissions[0].context.story).toEqual({
      variant_id: "", revision: "", node_ids: [], purpose: "design",
    });
    expect(submissions[0].story_action).toBe("create");
    await expect(dialog.getByRole("alert")).toContainText("隔离受理失败");
    await expect(dialog.getByLabel("创作要求")).toHaveValue("根据人物设定模块，建立湿手套的发现与对质。");
    await dialog.getByRole("button", { name: "取消", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await expect(page.locator(".chat-input")).toHaveValue("普通对话还没发送的草稿");
    const variants = await page.request.get(`/api/file-workspaces/${book.id}/story/variants`);
    expect((await variants.json()).variants).toEqual([]);
  });
}

test("空白构思读取失败时显示错误并可重新读取", async ({ page, book }) => {
  let failRead = true;
  await page.route(`**/api/file-workspaces/${book.id}/story/variants`, async route => {
    if (!failRead) return route.fallback();
    await route.fulfill({ status: 503, json: { error: {
      code: "STORY_READ_FAILED", where: "构思", why: "隔离读取失败", fix: "重新读取构思",
    } } });
  });
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("link", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  await expect(surface.getByRole("alert")).toContainText("隔离读取失败");
  await expect(surface.getByRole("button", { name: "创建构思", exact: true })).toHaveCount(0);
  failRead = false;
  await surface.getByRole("button", { name: "重新读取", exact: true }).click();
  await expect(surface.getByRole("button", { name: "创建构思", exact: true })).toBeVisible();
});

test("构思面只保留悬疑视图，时间轴节点用于过滤左栏线索", async ({ page, book }) => {
  const seeded = await page.request.post(
    `/api/file-workspaces/${book.id}/story/changes`,
    {
      data: {
        variant_id: "main",
        expected_revision: "empty",
        request_key: `seed-story-${book.id}`,
        operations: NODES,
      },
    },
  );
  expect(seeded.ok(), await seeded.text()).toBeTruthy();

  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("link", { name: "构思", exact: true }).click();

  const surface = page.getByRole("region", { name: "构思结构" });
  await expect(surface.getByRole("button", { name: "抵达" })).toBeVisible();
  await expect(surface.getByRole("button", { name: "对质" })).toBeVisible();
  await expect(surface.getByRole("button", { name: "结构视图" })).toHaveCount(0);
  await expect(surface.getByRole("group", { name: "阶段" })).toHaveCount(0);
  await expect(surface.getByLabel("节点详情", { exact: true })).toHaveCount(0);
  await expect(surface.getByRole("button", { name: /备选|试读|候选正文|采用|比较/ })).toHaveCount(0);
  await expect(surface.getByRole("tablist", { name: "方案" })).toHaveCount(0);

  const clues = page.getByRole("region", { name: "全书线索" });
  await surface.getByRole("button", { name: /^手套/ }).click();
  await expect(clues.getByRole("heading", { name: "手套" })).toBeVisible();
  await surface.getByRole("button", { name: "抵达" }).click();
  await expect(clues.getByText("与「抵达」相关")).toBeVisible();
  await expect(clues.getByRole("heading", { name: "手套" })).toBeVisible();

  await surface.getByRole("button", { name: "对质" }).click();
  await expect(clues.getByText("与「对质」相关")).toBeVisible();
  await expect(clues.getByRole("heading", { name: "手套" })).toHaveCount(0);
  await expect(clues.getByText("钥匙")).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/workspaces/${book.id}/writing`));
});

test("窄屏与大字号下构思时间轴可读，页面不横向溢出", async ({ page, book }) => {
  const api = (path: string, init?: Parameters<ReturnType<typeof page.request.get>>[1]) =>
    page.request.fetch(`/api/file-workspaces/${book.id}${path}`, init);
  await api("/story/changes", {
    method: "post",
    data: {
      variant_id: "main",
      expected_revision: "empty",
      request_key: `seed-narrow-${book.id}`,
      operations: [
        { op: "add_stage", stage: { id: "s1", title: "开端", order: 0 } },
        { op: "add_stage", stage: { id: "s2", title: "调查", order: 1 } },
        {
          op: "add_node",
          node: { id: "n1", title: "抵达", action: "侦探在前台看到一双手套", stage_id: "s1" },
        },
        {
          op: "add_node",
          node: { id: "n2", title: "对质", action: "他把手套放在桌上", stage_id: "s2" },
        },
        { op: "connect", edge: { id: "e1", from_id: "n1", to_id: "n2", kind: "next" } },
      ],
    },
  });

  const output = resolve(
    process.env.HASTINGS_E2E_SCREENSHOT_DIR ?? "../data/test-tmp/story-structure",
  );
  mkdirSync(output, { recursive: true });
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("link", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  await expect(surface.getByRole("button", { name: "对质" })).toBeVisible();
  await expect(surface.locator(".story-mystery-stage-group")).toHaveCount(2);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("combobox", { name: "调整字体大小" }).selectOption("150");
  await page.keyboard.press("Escape");
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(surface.getByRole("button", { name: "对质" })).toBeVisible();
  await page.screenshot({ path: join(output, "story-390-150.png") });
});

test("全书线索与时间轴节点联动：节点筛选线索，使用位置可回到另一节点", async ({ page, book }) => {
  const api = (path: string, init?: Parameters<ReturnType<typeof page.request.get>>[1]) =>
    page.request.fetch(`/api/file-workspaces/${book.id}${path}`, init);
  const prose = "侦探在走廊看到没有响的木板。";
  const quote = "没有响的木板";
  const path = "正文/0001 走廊.md";
  mkdirSync(join(book.path, "正文"), { recursive: true });
  writeFileSync(join(book.path, path), prose, "utf8");
  const digest = createHash("sha256").update(prose, "utf8").digest("hex");
  const start = prose.indexOf(quote);
  const seeded = await api("/story/changes", {
    method: "post",
    data: {
      variant_id: "main",
      expected_revision: "empty",
      request_key: `linkage-${book.id}`,
      operations: [
        { op: "add_stage", stage: { id: "s1", title: "当晚", order: 0, summary: "发现异样" } },
        { op: "add_stage", stage: { id: "s2", title: "调查", order: 1, summary: "核对路线" } },
        { op: "add_thread", thread: { id: "t1", title: "物证", order: 0, goal: "确认走廊是否有人经过" } },
        { op: "add_thread", thread: { id: "t2", title: "调查", order: 1, goal: "核对路线" } },
        { op: "create_clue_identity", temp_id: "board", title: "没有响的木板" },
        { op: "upsert_clue_plan", clue_id: "board", plan: {
          author_truth: null, intention: "让调查者怀疑有人熟悉走廊结构", resolution_intent: "explain",
        } },
        { op: "add_node", node: { id: "n1", title: "发现异样", action: "调查者注意到木板没有响",
          change: "调查者开始怀疑有人熟悉走廊结构", stage_id: "s1", thread_ids: ["t1"],
          clue_uses: [{ clue_id: "board", roles: ["plant"], presentation: quote, mode: "narration" }] } },
        { op: "add_node", node: { id: "n2", title: "核对路线", action: "调查者比较不同人的路线",
          change: "时间差指向熟悉走廊的人", stage_id: "s2", thread_ids: ["t2"],
          clue_uses: [{ clue_id: "board", roles: ["resolve"], presentation: "木板解释了路线差", mode: "narration" }] } },
        { op: "connect", edge: { id: "e1", from_id: "n1", to_id: "n2", kind: "next", reason: "物证引出路线核对" } },
        { op: "bind_prose", binding: { id: "b1", node_id: "n1", document_id: "doc", path,
          digest, start, end: start + quote.length, quote, observation: "走廊木板" } },
      ],
    },
  });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();

  await page.goto(`/#/workspaces/${book.id}/writing`);
  const writes: string[] = [];
  page.on("request", request => {
    if (request.method() !== "GET" && /\/(story\/changes|conversations)/.test(request.url())) {
      writes.push(request.url());
    }
  });
  await page.getByRole("link", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  const clues = page.getByRole("region", { name: "全书线索" });

  await surface.getByRole("button", { name: "发现异样", exact: true }).click();
  await expect(clues.getByText("与「发现异样」相关")).toBeVisible();
  await expect(clues.getByRole("heading", { name: "没有响的木板" })).toBeVisible();
  await expect(clues.getByRole("button", { name: "核对路线", exact: true })).toBeVisible();
  await clues.getByRole("button", { name: "核对路线", exact: true }).click();
  await clues.getByRole("button", { name: "返回列表", exact: true }).click();
  await expect(clues.getByRole("heading", { name: "与「核对路线」相关" })).toBeVisible();
  expect(writes).toEqual([]);
});

test("三线总览在 1440 与 1024 宽度可读", async ({ page, book }) => {
  const seeded = await page.request.post(`/api/file-workspaces/${book.id}/story/changes`, {
    data: {
      variant_id: "main",
      expected_revision: "empty",
      request_key: `author-${book.id}`,
      operations: [
        { op: "add_stage", stage: { id: "s1", title: "开端", order: 0, summary: "三条线分别建立" } },
        { op: "add_stage", stage: { id: "s2", title: "调查", order: 1, summary: "两线汇合" } },
        { op: "add_stage", stage: { id: "s3", title: "收束", order: 2, summary: "共同指向结局" } },
        { op: "add_thread", thread: { id: "t1", title: "物证", order: 0, goal: "找到与证词不符的物证" } },
        { op: "add_thread", thread: { id: "t2", title: "证词", order: 1, goal: "核对每个人的时间" } },
        { op: "add_thread", thread: { id: "t3", title: "调查", order: 2, goal: "把线索放到同一时间线" } },
        { op: "add_node", node: { id: "n1", title: "发现异样", action: "木板没有响", change: "开始怀疑有人熟悉走廊", stage_id: "s1", thread_ids: ["t1"] } },
        { op: "add_node", node: { id: "n2", title: "核对证词", action: "两名证人的时间对不上", change: "时间差成为问题", stage_id: "s1", thread_ids: ["t2"] } },
        { op: "add_node", node: { id: "n3", title: "重问目击者", action: "目击者承认只听到声音", change: "目击证词被削弱", stage_id: "s1", thread_ids: ["t3"] } },
        { op: "add_node", node: { id: "n4", title: "汇合时间线", action: "三线结果放在一起核对", change: "指向同一段无人经过的时间", stage_id: "s2", thread_ids: ["t1", "t2", "t3"] } },
        { op: "add_node", node: { id: "n5", title: "确认路线", action: "调查者还原当晚路线", change: "熟悉走廊的人被锁定", stage_id: "s3", thread_ids: ["t3"] } },
        { op: "connect", edge: { id: "e1", from_id: "n1", to_id: "n4", kind: "next", reason: "物证进入汇合核对" } },
        { op: "connect", edge: { id: "e2", from_id: "n2", to_id: "n4", kind: "next", reason: "证词进入汇合核对" } },
        { op: "connect", edge: { id: "e3", from_id: "n3", to_id: "n5", kind: "next", reason: "目击说法收束" } },
        { op: "connect", edge: { id: "e4", from_id: "n4", to_id: "n5", kind: "next", reason: "汇合结果指向路线" } },
      ],
    },
  });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();

  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("link", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  await expect(surface.getByRole("button", { name: "汇合时间线" })).toHaveCount(1);
  await expect(surface.locator(".story-mystery-stage-group")).toHaveCount(3);
  await page.screenshot({ path: resolve("../data/test-tmp/story-structure/v103-author-1440.png") });
  await page.setViewportSize({ width: 1024, height: 800 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(surface.getByRole("button", { name: "汇合时间线" })).toBeVisible();
  await page.screenshot({ path: resolve("../data/test-tmp/story-structure/v103-author-1024.png") });
});

test("快速切换作品不会把上一本的节点带到当前构思", async ({ page, book }) => {
  const parent = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "switch-books");
  mkdirSync(parent, { recursive: true });
  const second = await page.request.post("/api/file-workspaces", {
    data: { path: join(mkdtempSync(join(parent, "second-")), "第二本"), create: true },
  });
  expect(second.ok(), await second.text()).toBeTruthy();
  const other = await second.json();
  const seed = async (workspaceId: string, title: string) => {
    const response = await page.request.post(`/api/file-workspaces/${workspaceId}/story/changes`, {
      data: { variant_id: "main", expected_revision: "empty", request_key: `switch-${workspaceId}`,
        operations: [{ op: "add_node", node: { id: "only", title, action: "只属于这一本" } }] },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
  };
  await seed(book.id, "第一本节点");
  await seed(other.id, "第二本节点");

  await page.goto(`/#/workspaces/${book.id}/writing?mode=story`);
  const surface = page.getByRole("region", { name: "构思结构" });
  await expect(surface.getByRole("button", { name: "第一本节点" })).toBeVisible();

  await page.goto(`/#/workspaces/${other.id}/writing?mode=story`);
  await expect(surface.getByRole("button", { name: "第二本节点" })).toBeVisible();
  await expect(surface.getByRole("button", { name: "第一本节点" })).toHaveCount(0);

  await page.goto(`/#/workspaces/${book.id}/writing?mode=story`);
  await expect(surface.getByRole("button", { name: "第一本节点" })).toBeVisible();
  await expect(surface.getByRole("button", { name: "第二本节点" })).toHaveCount(0);
});
