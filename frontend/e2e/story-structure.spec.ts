import { test, expect } from "./fixtures/frontendExperience";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

/**
 * 构思面：真实服务响应驱动，不 mock 结构接口。
 *
 * 只断言可判定的事实：节点标题来自服务端、点开有安排与真相、建备选后多出一个方案、
 * 备选与主方案的差异面板出现。视觉细节不在这里评判。
 */

const NODES = [
  { op: "create_clue_identity", temp_id: "gloves", title: "手套" },
  {
    op: "upsert_clue_plan",
    clue_id: "gloves",
    plan: { author_truth: "管家在雪地里搬过尸体", resolution_intent: "explain" },
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
      action: "他把手套放在桌上",
      clue_uses: [{ clue_id: "gloves", roles: ["resolve"], presentation: "他说出手套的来历", mode: "narration" }],
    },
  },
  {
    op: "connect",
    edge: { id: "e1", from_id: "n1", to_id: "n2", kind: "next" },
  },
];

test("故事线表头纵向冻结，横向滚动和缩放保持列对齐", async ({ page, book }) => {
  const threads = ["a", "b", "c", "d"];
  const operations = [
    ...threads.map((id, order) => ({ op: "add_thread", thread: { id, title: `故事线 ${id}`, order, goal: "核对时间与证据", obstacle: "关键记录缺失" } })),
    ...threads.flatMap(t => Array.from({ length: 20 }, (_, i) => ({ op: "add_node", node: {
      id: `${t}${i}`, title: `${t} 的推进 ${i}`, action: "继续核对本条故事线中的证据。", thread_ids: [t], primary_thread_id: t, reveal_order: i,
    } }))),
  ];
  const base = `/api/file-workspaces/${book.id}/story`;
  const saved = await page.request.post(`${base}/changes`, { data: { variant_id: "main", expected_revision: "empty", request_key: `frozen-${book.id}`, operations } });
  expect(saved.ok(), await saved.text()).toBeTruthy();
  const before = await page.request.get(`${base}/variants/main`).then(r => r.json());
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "构思", exact: true }).click();
  const canvas = page.locator(".story-canvas");
  const header = canvas.getByRole("region", { name: "故事线表头" });
  const heading = header.locator('[data-thread-id="c"]');
  const card = canvas.locator('.react-flow__node[data-id="c19"]');
  await expect(header).toBeVisible();
  const geometry = async () => {
    const h = (await heading.boundingBox())!, c = (await card.boundingBox())!;
    return { top: h.y, left: h.x, width: h.width, cardLeft: c.x, cardWidth: c.width };
  };
  await expect.poll(async () => Math.abs((await geometry()).left - (await geometry()).cardLeft)).toBeLessThan(1);
  const first = await geometry();
  await canvas.getByRole("region", { name: "画布纵向滚动" }).press("End");
  await expect.poll(async () => Math.abs((await geometry()).top - first.top)).toBeLessThan(1);
  const horizontal = canvas.getByRole("region", { name: "画布横向滚动" });
  await horizontal.evaluate(element => { element.scrollLeft += 150; });
  await expect.poll(async () => (await geometry()).left).toBeLessThan(first.left - 100);
  await expect.poll(async () => { const g = await geometry(); return Math.abs(g.left - g.cardLeft); }).toBeLessThan(1);
  await canvas.getByRole("button", { name: "放大画布", exact: true }).click();
  await expect.poll(async () => (await geometry()).width).toBeGreaterThan(first.width + 20);
  await expect.poll(async () => { const g = await geometry(); return Math.abs(g.width - g.cardWidth) + Math.abs(g.left - g.cardLeft); }).toBeLessThan(2);
  await heading.locator("summary").click();
  await expect(heading).toContainText("关键记录缺失");
  await expect(heading.locator("details")).toHaveAttribute("open", "");
  await page.screenshot({ path: "test-results/story-frozen-headings.png" });
  expect(await page.request.get(`${base}/variants/main`).then(r => r.json())).toEqual(before);
});

test("无模块来源的草稿约定可由作者确认，不自动调用模型", async ({ page, book }) => {
  const state = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "state");
  execFileSync("uv", ["run", "--directory", "..", "python", resolve("e2e/fixtures/story_draft_review.py"), state, book.id], { windowsHide: true });
  await page.route("**/api/operations/op-review/continue", route => route.fulfill({
    status: 409, contentType: "application/json", body: JSON.stringify({ error: { code: "TEST_STOP", message: "测试在作者确认后停止" } }),
  }));
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "构思", exact: true }).click();
  await expect(page.locator(".story-draft")).toContainText("至少六名嫌疑人");
  const response = page.waitForResponse(r => r.url().endsWith("/confirm-constraints"));
  await page.getByRole("button", { name: "确认这些必需约定并继续" }).click();
  const confirmed = await response;
  expect(confirmed.ok(), await confirmed.text()).toBeTruthy();
  expect((await confirmed.json()).confirmed).toBe(1);
});

test("单卡拖动与键盘移动保持列归属，整图仍可平移和选中", async ({ page, book }) => {
  const operations = [
    ...["a", "b", "c"].map((id, order) => ({ op: "add_thread", thread: { id, title: `线 ${id}`, order } })),
    ...["a", "b", "c"].map(id => ({ op: "add_node", node: { id, title: `节点 ${id}`, action: "保留归属", thread_ids: [id], primary_thread_id: id } })),
  ];
  const base = `/api/file-workspaces/${book.id}/story`;
  const saved = await page.request.post(`${base}/changes`, { data: { variant_id: "main", expected_revision: "empty", request_key: `no-drag-${book.id}`, operations } });
  expect(saved.ok(), await saved.text()).toBeTruthy();
  const graph = await page.request.get(`${base}/variants/main`).then(r => r.json());
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  const card = surface.locator('.react-flow__node[data-id="a"]');
  const heading = surface.locator('.react-flow__node[data-id="thread:a"]');
  await expect(card).toBeVisible();
  const offset = async () => {
    const a = await card.boundingBox(), h = await heading.boundingBox();
    return [Math.round(a!.x - h!.x), Math.round(a!.y - h!.y)];
  };
  await expect.poll(async () => (await offset())[0]).toBe(0);
  const initial = await offset();
  const box = (await card.boundingBox())!;
  await page.mouse.move(box.x + 40, box.y + 30);
  await page.mouse.down();
  await page.mouse.move(box.x + 340, box.y + 120, { steps: 15 });
  await page.mouse.up();
  await expect.poll(offset).toEqual(initial);
  await card.focus();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowDown");
  await expect.poll(offset).toEqual(initial);
  const viewport = surface.locator(".react-flow__viewport");
  const oldViewport = await viewport.getAttribute("style");
  const canvas = (await surface.locator(".story-canvas").boundingBox())!;
  // 表头区域现在固定；从表头下方的画布空白处平移。
  await page.mouse.move(canvas.x + 30, canvas.y + 180);
  await page.mouse.down();
  await page.mouse.move(canvas.x + 90, canvas.y + 220, { steps: 8 });
  await page.mouse.up();
  await expect.poll(() => viewport.getAttribute("style")).not.toBe(oldViewport);
  await expect.poll(offset).toEqual(initial);
  await surface.getByRole("button", { name: "节点 a", exact: true }).click();
  await expect(surface.getByRole("complementary", { name: "节点详情" })).toContainText("保留归属");
  const after = await page.request.get(`${base}/variants/main`).then(r => r.json());
  expect(after).toEqual(graph);
});

test("多线长摘要在各字号和窄屏下无矩形遮挡，折叠保留跨阶段连接", async ({ page, book }) => {
  test.setTimeout(90_000);
  const operations = [
    ...["a", "b", "c"].map((id, order) => ({ op: "add_thread", thread: { id, title: `故事线 ${id}`, order, goal: "追查各自的问题", obstacle: "证据不全", intended_resolution: "共同解释" } })),
    ...["s1", "s2", "empty"].map((id, order) => ({ op: "add_stage", stage: { id, title: `阶段 ${id}`, order, summary: "大雪封路，调查各自推进，每一条线都改变另一条线的判断。".repeat(8) } })),
    ...["a", "b", "c"].flatMap(id => [1, 2].map(i => ({ op: "add_node", node: { id: `${id}${i}`, title: `${id} 的变化 ${i}`, action: "完整行动留在详情。".repeat(30), change: "关键证据改变了当前判断。", stage_id: `s${i}`, thread_ids: [id, ...["a", "b", "c"].filter(t => t !== id)], primary_thread_id: id, reveal_order: i } }))),
    { op: "add_node", node: { id: "join", title: "三线共同收束", action: "核对全部证据", change: "共同确定行动机会", stage_id: "s2", thread_ids: ["a", "b", "c"], primary_thread_id: "a", is_convergence: true } },
    ...["a", "b", "c"].flatMap(id => [
      { op: "connect", edge: { id: `${id}-progress`, from_id: `${id}1`, to_id: `${id}2`, kind: "next", reason: "该线的证据推动下一次行动" } },
      { op: "connect", edge: { id: `${id}-join`, from_id: `${id}2`, to_id: "join", kind: "next", reason: "共同作用于结论" } },
    ]),
  ];
  const saved = await page.request.post(`/api/file-workspaces/${book.id}/story/changes`, { data: { variant_id: "main", expected_revision: "empty", request_key: `geometry-${book.id}`, operations } });
  expect(saved.ok(), await saved.text()).toBeTruthy();
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  await expect(surface.locator(".story-node")).toHaveCount(7);
  const stageLeft = async () => {
    const canvas = await surface.locator(".story-canvas").boundingBox();
    const stage = await surface.locator(".story-stage-band").first().boundingBox();
    return Math.round(stage!.x - canvas!.x);
  };
  await expect.poll(stageLeft).toBe(20);
  const overlap = () => surface.locator(".react-flow__node").evaluateAll(elements => {
    const boxes = elements.map(e => ({ id: e.getAttribute("data-id"), r: e.getBoundingClientRect() }));
    return boxes.flatMap((a, i) => boxes.slice(i + 1).filter(b => a.r.left < b.r.right - 1 && b.r.left < a.r.right - 1 && a.r.top < b.r.bottom - 1 && b.r.top < a.r.bottom - 1).map(b => `${a.id}/${b.id}`));
  });
  for (const width of [1440, 1024, 390]) for (const scale of [1, 1.5, 2]) {
    await page.setViewportSize({ width, height: 900 });
    await page.evaluate(scale => document.documentElement.style.setProperty("--font-scale", String(scale)), scale);
    await expect.poll(overlap, { message: `width=${width} scale=${scale}` }).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.evaluate(() => document.documentElement.style.setProperty("--font-scale", "1"));
  await expect.poll(stageLeft).toBe(20);
  await surface.getByRole("button", { name: "阶段 s2", exact: true }).click();
  await expect(surface.locator(".story-node")).toHaveCount(4);
  await expect(surface.locator('.react-flow__edge[data-id="a-progress"]')).toHaveCount(1);
  await expect.poll(overlap).toEqual([]);
  await surface.getByRole("button", { name: "全部", exact: true }).click();
  await expect(surface.locator(".story-node")).toHaveCount(7);
  await expect(surface.getByRole("button", { name: "三线共同收束", exact: true })).toHaveCount(1);
  await expect.poll(overlap).toEqual([]);
  await page.screenshot({ path: "test-results/story-multiline-remediation.png" });
});

test("长线路可用双向滚动条导航，缩放可查看完整全貌并恢复原尺寸", async ({ page, book }) => {
  const chain = Array.from({ length: 30 }, (_, index) => ({ op: "add_node", node: {
    id: `long-${index}`, title: `长线路 ${index + 1}`, action: "保留这一处的安排。",
  } }));
  const branches = Array.from({ length: 8 }, (_, index) => ({ op: "add_node", node: {
    id: `branch-${index}`, title: `并行安排 ${index + 1}`, action: "在结尾共同回应。",
  } }));
  const links = Array.from({ length: 29 }, (_, index) => ({ op: "connect", edge: {
    id: `long-edge-${index}`, from_id: `long-${index}`, to_id: `long-${index + 1}`, kind: "next",
  } }));
  const branchLinks = branches.flatMap((branch, index) => [
    { op: "connect", edge: { id: `branch-in-${index}`, from_id: "long-0", to_id: branch.node.id, kind: "next" } },
    { op: "connect", edge: { id: `branch-out-${index}`, from_id: branch.node.id, to_id: "long-29", kind: "next" } },
  ]);
  const seeded = await page.request.post(`/api/file-workspaces/${book.id}/story/changes`, { data: {
    variant_id: "main", expected_revision: "empty", request_key: `scroll-${book.id}`,
    operations: [...chain, ...branches, ...links, ...branchLinks],
  } });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "构思", exact: true }).click();
  const canvas = page.locator(".story-canvas");
  const horizontal = canvas.getByRole("region", { name: "画布横向滚动" });
  const vertical = canvas.getByRole("region", { name: "画布纵向滚动" });
  const viewport = () => canvas.locator(".react-flow__viewport").evaluate(element => {
    const matrix = new DOMMatrix(getComputedStyle(element).transform);
    return { x: matrix.m41, y: matrix.m42, zoom: matrix.a };
  });
  await expect.poll(() => vertical.evaluate(element => element.scrollHeight - element.clientHeight)).toBeGreaterThan(2000);
  await expect.poll(() => horizontal.evaluate(element => element.scrollWidth - element.clientWidth)).toBeGreaterThan(1000);
  const before = await viewport();
  await vertical.press("End");
  await expect.poll(async () => (await viewport()).y).toBeLessThan(before.y - 2000);
  expect((await viewport()).zoom).toBeCloseTo(before.zoom);
  const bottom = await vertical.evaluate(element => element.scrollTop);
  const canvasBounds = (await canvas.boundingBox())!;
  await page.mouse.move(canvasBounds.x + canvasBounds.width / 2, canvasBounds.y + 60);
  await page.mouse.wheel(0, -200);
  await expect.poll(() => vertical.evaluate(element => element.scrollTop)).toBeLessThan(bottom);
  const previousX = (await viewport()).x;
  await horizontal.evaluate(element => { element.scrollLeft = element.scrollWidth; });
  await expect.poll(async () => (await viewport()).x).toBeLessThan(previousX - 200);
  await canvas.getByRole("button", { name: "查看全貌", exact: true }).click();
  await expect.poll(async () => (await viewport()).zoom).toBeLessThan(0.6);
  await expect.poll(() => canvas.locator(".react-flow__node").evaluateAll(elements => {
    const frame = elements[0].closest(".react-flow")!.getBoundingClientRect();
    return elements.every(element => {
      const rect = element.getBoundingClientRect();
      return rect.left >= frame.left && rect.right <= frame.right && rect.top >= frame.top && rect.bottom <= frame.bottom;
    });
  })).toBeTruthy();
  await page.screenshot({ path: "test-results/story-canvas-overview.png" });
  await canvas.getByRole("button", { name: "恢复100%缩放", exact: true }).click();
  await expect.poll(async () => (await viewport()).zoom).toBeCloseTo(1);
  for (let index = 0; index < 6; index++) await canvas.getByRole("button", { name: "放大画布", exact: true }).click();
  await expect.poll(async () => (await viewport()).zoom).toBeGreaterThan(1.6);
  await page.setViewportSize({ width: 390, height: 900 });
  await expect(vertical).toBeVisible();
  await expect(horizontal).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test("纵向画布展开分支并汇合，详情在下方且专注模式扩大画布", async ({ page, book }) => {
  const seeded = await page.request.post(`/api/file-workspaces/${book.id}/story/changes`, { data: {
    variant_id: "main", expected_revision: "empty", request_key: `vertical-${book.id}`,
    operations: [...NODES,
      { op: "add_node", node: { id: "n3", title: "核对订房记录与入住经过", action: "核对账簿记录。".repeat(30) } },
      { op: "add_node", node: { id: "n4", title: "行动机会汇合", action: "一起核对案发夜的行动机会" } },
      ...[["fork", "n1", "n3"], ["join1", "n2", "n4"], ["join2", "n3", "n4"]].map(([id, from_id, to_id]) => ({ op: "connect", edge: { id, from_id, to_id, kind: "next" } })),
      { op: "connect", edge: { id: "answer", from_id: "n4", to_id: "n1", kind: "answer", reason: "后文回应湿手套" } },
    ],
  } });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  const positions = () => surface.locator(".react-flow__node").evaluateAll(elements => Object.fromEntries(elements.map(element => {
    const bounds = element.getBoundingClientRect();
    return [element.getAttribute("data-id"), { x: bounds.x, y: bounds.y, w: bounds.width, h: bounds.height }];
  })));
  await expect.poll(async () => { const nodes = await positions(); return Boolean(nodes.n4 && nodes.n4.y > nodes.n2.y && nodes.n2.y > nodes.n1.y); }).toBeTruthy();
  const nodes = await positions();
  expect(Math.abs(nodes.n2.y - nodes.n3.y)).toBeLessThan(2);
  expect(Math.abs(nodes.n2.x - nodes.n3.x)).toBeGreaterThanOrEqual(280);
  expect(nodes.n4.y).toBeGreaterThan(nodes.n3.y + nodes.n3.h);
  await expect(page.locator("#writing-directory")).toBeHidden();
  await surface.getByRole("button", { name: "抵达", exact: true }).click();
  const canvas = surface.locator(".story-canvas");
  const detail = surface.getByLabel("节点详情", { exact: true });
  await expect(detail).toBeVisible();
  const canvasBounds = (await canvas.boundingBox())!;
  expect((await detail.boundingBox())!.y).toBeGreaterThanOrEqual(canvasBounds.y + canvasBounds.height);
  await page.getByRole("button", { name: "专注构思", exact: true }).click();
  await expect.poll(async () => (await canvas.boundingBox())!.width).toBeGreaterThan(canvasBounds.width + 200);
  await expect(surface.getByRole("button", { name: "抵达", exact: true })).toHaveAttribute("aria-pressed", "true");
  await detail.getByRole("button", { name: "收起节点详情" }).click();
  await expect(detail).toBeHidden();
  await page.screenshot({ path: "test-results/story-vertical-1440.png" });
  await page.setViewportSize({ width: 390, height: 900 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await expect(canvas).toBeVisible();
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
    await page.getByRole("button", { name: "构思", exact: true }).click();
    const surface = page.getByRole("region", { name: "构思结构" });
    const start = surface.getByRole("button", { name: "创建构思", exact: true });
    await expect(start).toBeVisible();
    await expect(surface.getByRole("tablist", { name: "方案" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "构思", exact: true })).toHaveAttribute("aria-pressed", "true");
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
  await page.getByRole("button", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  await expect(surface.getByRole("alert")).toContainText("隔离读取失败");
  await expect(surface.getByRole("button", { name: "创建构思", exact: true })).toHaveCount(0);
  failRead = false;
  await surface.getByRole("button", { name: "重新读取", exact: true }).click();
  await expect(surface.getByRole("button", { name: "创建构思", exact: true })).toBeVisible();
});

test("构思面显示节点详情且不再提供备选、试读和候选入口", async ({ page, book }) => {
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
  await page.getByRole("button", { name: "构思", exact: true }).click();

  const surface = page.getByRole("region", { name: "构思结构" });
  await expect(surface.getByRole("button", { name: "抵达" })).toBeVisible();
  await expect(surface.getByRole("button", { name: "对质" })).toBeVisible();

  await surface.getByRole("button", { name: "抵达" }).click();
  await expect(surface.getByRole("heading", { name: "抵达" })).toBeVisible();
  await expect(surface.getByLabel("节点详情", { exact: true }).getByText("侦探在前台看到一双手套")).toBeVisible();
  await surface.getByText("真实作用与后续回应", { exact: true }).click();
  await expect(surface.getByText("真相：管家在雪地里搬过尸体")).toBeVisible();

  await expect(surface.getByRole("button", { name: /备选|试读|候选正文|采用|比较/ })).toHaveCount(0);
  await expect(surface.getByRole("tablist", { name: "方案" })).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(`/workspaces/${book.id}/writing`));

});

test("阶段折叠与引用到对话", async ({ page, book }) => {
  const api = (path: string, init?: Parameters<ReturnType<typeof page.request.get>>[1]) =>
    page.request.fetch(`/api/file-workspaces/${book.id}${path}`, init);
  // 每个用例都是新作品：先按同一份种子建立基础结构，再做片段浏览。
  await api("/story/changes", {
    method: "post",
    data: {
      variant_id: "main",
      expected_revision: "empty",
      request_key: `seed-base-${book.id}`,
      operations: [
        { op: "add_stage", stage: { id: "s1", title: "开端", order: 0, summary: "抵达与发现" } },
        { op: "add_stage", stage: { id: "s2", title: "调查", order: 1, summary: "对质" } },
        { op: "create_clue_identity", temp_id: "gloves", title: "手套" },
        {
          op: "upsert_clue_plan",
          clue_id: "gloves",
          plan: { author_truth: "管家在雪地里搬过尸体", resolution_intent: "explain" },
        },
        {
          op: "add_node",
          node: {
            id: "n1",
            title: "抵达",
            action: "侦探在前台看到一双手套",
            stage_id: "s1",
            clue_uses: [{ clue_id: "gloves", presentation: "手套还在滴水", mode: "narration" }],
          },
        },
        {
    op: "add_node",
    node: {
      id: "n2",
      title: "对质",
      action: "他把手套放在桌上",
      stage_id: "s2",
      clue_uses: [{ clue_id: "gloves", roles: ["resolve"], presentation: "他说出手套的来历", mode: "narration" }],
    },
  },
        { op: "connect", edge: { id: "e1", from_id: "n1", to_id: "n2", kind: "next" } },
      ],
    },
  });

  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  const groups = surface.getByRole("group", { name: "阶段" });
  await expect(groups).toBeVisible();

  await groups.getByRole("button", { name: "开端", exact: true }).click();
  await expect(surface.getByRole("button", { name: "抵达" })).toBeVisible();
  await expect(surface.getByRole("button", { name: "对质" })).toHaveCount(0);

  await groups.getByRole("button", { name: "全部", exact: true }).click();
  await expect(surface.getByRole("button", { name: "对质" })).toBeVisible();

  await surface.getByRole("button", { name: "抵达", exact: true }).click();
  await surface.getByRole("button", { name: "引用到对话" }).click();
  await expect(page.getByLabel("引用构思")).toContainText("抵达");
  await page.getByRole("button", { name: "移除构思引用 抵达", exact: true }).click();
  await expect(page.getByLabel("引用构思")).toHaveCount(0);

});

test("窄屏与大字号下构思面可读，结构不横向溢出", async ({ page, book }) => {
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
  await page.getByRole("button", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  await expect(surface.getByRole("button", { name: "对质" })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("combobox", { name: "调整字体大小" }).selectOption("150");
  await page.keyboard.press("Escape");
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  // 详情在窄屏下移到图下方：点开节点后仍能读到安排与操作按钮。
  await surface.getByRole("button", { name: "对质" }).click();
  await expect(surface.getByRole("heading", { name: "对质" })).toBeVisible();
  await expect(surface.getByRole("button", { name: "引用到对话" })).toBeInViewport();
  await page.screenshot({ path: join(output, "story-390-150.png") });
});

test("长篇结构按阶段折叠，切换阶段可查看跨阶段关系", async ({ page, book }) => {
  test.setTimeout(120_000);
  const stages = Array.from({ length: 30 }, (_, index) => {
    const order = index;
    return { op: "add_stage", stage: { id: `stage-${order + 1}`, title: `章节 ${String(order + 1).padStart(2, "0")}`, order } };
  });
  const nodes = Array.from({ length: 300 }, (_, index) => {
    const number = String(index + 1).padStart(3, "0");
    const stage = Math.floor(index / 10) + 1;
    return { op: "add_node", node: { id: `node-${number}`, title: `节点 ${number}`,
      action: `第 ${stage} 段发生的事 ${number}`, stage_id: `stage-${stage}` } };
  });
  const edges = Array.from({ length: 299 }, (_, index) => ({
    op: "connect", edge: { id: `edge-${index + 1}`,
      from_id: `node-${String(index + 1).padStart(3, "0")}`,
      to_id: `node-${String(index + 2).padStart(3, "0")}`, kind: "next" },
  }));
  edges.push({ op: "connect", edge: { id: "cross-reframe", from_id: "node-300",
    to_id: "node-001", kind: "reframe" } });
  for (let chapter = 1; chapter <= 30; chapter++) {
    const label = String(chapter).padStart(4, "0");
    writeFileSync(join(book.path, "正文", `${label} 章节.md`), `# 第 ${chapter} 章

正文。
`, "utf8");
  }
  const seeded = await page.request.post(`/api/file-workspaces/${book.id}/story/changes`, {
    data: { variant_id: "main", expected_revision: "empty", request_key: `long-${book.id}`,
      operations: [...stages, ...nodes, ...edges] },
  });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();

  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  const groups = surface.getByRole("group", { name: "阶段" });
  await groups.getByRole("button", { name: "章节 01", exact: true }).click();
  await expect(surface.locator(".story-node")).toHaveCount(10);
  await expect(surface).toContainText("290 个节点在其他阶段");
  await groups.getByRole("button", { name: "章节 30", exact: true }).click();
  await expect(surface.locator(".story-node")).toHaveCount(10);
  await surface.getByRole("button", { name: "节点 300", exact: true }).click();
  await expect(surface.getByLabel("节点详情", { exact: true })).toContainText("节点 001");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("combobox", { name: "调整字体大小" }).selectOption("150");
  await page.keyboard.press("Escape");
  await expect(groups).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});


test("全书线索与阶段联动：节点到线索到另一节点再到正文并返回", async ({ page, book }) => {
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
  await page.getByRole("button", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  await surface.getByRole("button", { name: "发现异样", exact: true }).click();
  await page.getByRole("button", { name: "全书线索", exact: true }).click();
  const clues = page.getByRole("region", { name: "全书线索" });
  await expect(clues.getByRole("button", { name: /^没有响的木板/ })).toBeVisible();
  await clues.getByRole("button", { name: /^没有响的木板/ }).click();
  await expect(clues.getByRole("heading", { name: "没有响的木板" })).toBeVisible();
  await clues.getByRole("button", { name: "核对路线", exact: true }).click();
  await expect(surface.getByRole("heading", { name: "核对路线" })).toBeVisible();

  await surface.getByRole("button", { name: "定位到发现异样", exact: true }).click();
  await expect(surface.locator(".story-binding")).toContainText(quote);
  await surface.locator(".story-binding").click();
  await page.getByRole("button", { name: "返回构思", exact: true }).click();
  await expect(surface).toBeVisible();
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
  await page.getByRole("button", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  await expect(surface.getByRole("button", { name: "汇合时间线" })).toHaveCount(1);
  await expect(surface.getByRole("group", { name: "阶段" }).getByRole("button")).toHaveCount(4);
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

  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  await expect(surface.getByRole("button", { name: "第一本节点" })).toBeVisible();

  await page.goto(`/#/workspaces/${other.id}/writing`);
  await page.getByRole("button", { name: "构思", exact: true }).click();
  await expect(surface.getByRole("button", { name: "第二本节点" })).toBeVisible();
  await expect(surface.getByRole("button", { name: "第一本节点" })).toHaveCount(0);

  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "构思", exact: true }).click();
  await expect(surface.getByRole("button", { name: "第一本节点" })).toBeVisible();
  await expect(surface.getByRole("button", { name: "第二本节点" })).toHaveCount(0);
});
