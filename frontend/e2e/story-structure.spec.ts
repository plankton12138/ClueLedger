import { test, expect } from "./fixtures/frontendExperience";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

/**
 * 构思面：真实服务响应驱动，不 mock 结构接口。
 *
 * 只断言可判定的事实：节点标题来自服务端、点开有安排与真相、建备选后多出一个方案、
 * 备选与主方案的差异面板出现。视觉细节不在这里评判。
 */

const NODES = [
  {
    op: "add_clue",
    clue: {
      id: "gloves",
      title: "手套",
      author_truth: "管家在雪地里搬过尸体",
      resolution: { intent: "explain", node_ids: ["n2"] },
    },
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
  { op: "add_node", node: { id: "n2", title: "对质", action: "他把手套放在桌上" } },
  {
    op: "connect",
    edge: { id: "e1", from_id: "n1", to_id: "n2", kind: "next" },
  },
];

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
  expect(Math.abs(nodes.n2.y + nodes.n2.h / 2 - nodes.n3.y - nodes.n3.h / 2)).toBeLessThan(2);
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

test("片段折叠与引用到对话", async ({ page, book }) => {
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
        {
          op: "add_clue",
          clue: {
            id: "gloves",
            title: "手套",
            author_truth: "管家在雪地里搬过尸体",
            resolution: { intent: "explain", node_ids: ["n2"] },
          },
        },
        {
          op: "add_node",
          node: {
            id: "n1",
            title: "抵达",
            action: "侦探在前台看到一双手套",
            clue_uses: [{ clue_id: "gloves", presentation: "手套还在滴水", mode: "narration" }],
          },
        },
        { op: "add_node", node: { id: "n2", title: "对质", action: "他把手套放在桌上" } },
        { op: "connect", edge: { id: "e1", from_id: "n1", to_id: "n2", kind: "next" } },
      ],
    },
  });
  const seeded = await api("/story/variants").then((r) => r.json());
  const seededMain = seeded.variants[0];

  // 给 n1 分组：片段按钮来自节点上的 group_id。
  await api("/story/changes", {
    method: "post",
    data: {
      variant_id: seededMain.id,
      expected_revision: seededMain.revision,
      request_key: `seed-groups-${book.id}`,
      operations: [
        {
          op: "update_node",
          node_id: "n1",
          node: { id: "n1", title: "抵达", action: "侦探在前台看到一双手套", group_id: "开端" },
        },
      ],
    },
  });

  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  const groups = surface.getByRole("group", { name: "片段" });
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
        {
          op: "add_node",
          node: { id: "n1", title: "抵达", action: "侦探在前台看到一双手套", group_id: "开端" },
        },
        { op: "add_node", node: { id: "n2", title: "对质", action: "他把手套放在桌上" } },
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

test("长篇结构默认只展开一个片段，切换片段可查看跨片段关系", async ({ page, book }) => {
  test.setTimeout(120_000);
  const nodes = Array.from({ length: 300 }, (_, index) => {
    const number = String(index + 1).padStart(3, "0");
    const chapter = String(Math.floor(index / 10) + 1).padStart(2, "0");
    return { op: "add_node", node: { id: `node-${number}`, title: `节点 ${number}`,
      action: `第 ${chapter} 章发生的事 ${number}`, group_id: `片段 ${chapter}` } };
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
    writeFileSync(join(book.path, "正文", `${label} 章节.md`), `# 第 ${chapter} 章\n\n正文。\n`, "utf8");
  }
  const seeded = await page.request.post(`/api/file-workspaces/${book.id}/story/changes`, {
    data: { variant_id: "main", expected_revision: "empty", request_key: `long-${book.id}`,
      operations: [...nodes, ...edges] },
  });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();

  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  const choice = surface.getByRole("combobox", { name: "选择片段" });
  await expect(choice).toHaveValue("片段 01");
  await expect(surface.locator(".story-node")).toHaveCount(10);
  await expect(surface).toContainText("290 个节点不在这个片段里");
  await choice.selectOption("片段 30");
  await expect(surface.locator(".story-node")).toHaveCount(10);
  await surface.getByRole("button", { name: "节点 300", exact: true }).click();
  await expect(surface.getByLabel("节点详情", { exact: true })).toContainText("node-001");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("combobox", { name: "调整字体大小" }).selectOption("150");
  await page.keyboard.press("Escape");
  await expect(choice).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
