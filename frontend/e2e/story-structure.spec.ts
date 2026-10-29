import { test, expect } from "./fixtures/frontendExperience";
import { mkdirSync } from "node:fs";
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

test("构思面显示结构、节点详情与备选差异", async ({ page, book }) => {
  const prefix = `**/api/file-workspaces/${book.id}/story`;
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
  await expect(surface.getByText("侦探在前台看到一双手套")).toBeVisible();
  await expect(surface.getByText("真相：管家在雪地里搬过尸体")).toBeVisible();

  // 建备选：只复制基线，不改主方案。
  await surface.getByRole("button", { name: "建备选" }).click();
  await expect(surface.getByRole("tab", { name: /备选/ })).toBeVisible();
  await expect(surface.getByRole("button", { name: "与主方案比较" })).toBeVisible();

  await surface.getByRole("button", { name: "与主方案比较" }).click();
  await expect(surface.getByRole("region", { name: "与主方案的差异" })).toBeVisible();

  // 主方案仍是原来的两个节点。
  await surface.getByRole("tab", { name: /主方案/ }).click();
  await expect(surface.getByRole("button", { name: "抵达" })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/workspaces/${book.id}/writing`));
  void prefix;
});

test("片段折叠、采用备选与引用到对话", async ({ page, book }) => {
  const api = (path: string, init?: Parameters<ReturnType<typeof page.request.get>>[1]) =>
    page.request.fetch(`http://127.0.0.1:8931/api/file-workspaces/${book.id}${path}`, init);
  // 每个用例都是新作品：先按同一份种子建立基础结构，再做片段与采用。
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

  // 建备选后给它加一个节点，再采用到主方案。
  await surface.getByRole("button", { name: "建备选" }).click();
  await expect(surface.getByRole("tab", { name: /备选/ })).toBeVisible();
  const alt = await api("/story/variants").then((r) => r.json()).then(
    (body: { variants: { id: string; revision: string; primary: boolean }[] }) =>
      body.variants.find((item) => !item.primary),
  );
  await api("/story/changes", {
    method: "post",
    data: {
      variant_id: alt.id,
      expected_revision: alt.revision,
      request_key: `alt-node-${book.id}`,
      operations: [{ op: "add_node", node: { id: "n9", title: "尾声", action: "雪停了" } }],
    },
  });

  await page.reload();
  await page.getByRole("button", { name: "构思", exact: true }).click();
  const surface2 = page.getByRole("region", { name: "构思结构" });
  await surface2.getByRole("tab", { name: /备选/ }).click();
  await surface2.getByRole("button", { name: "采用此方案" }).click();
  await expect(surface2.getByRole("region", { name: "采用结果" })).toContainText(
    "已复制到主方案",
  );

  // 引用到对话：草稿区出现构思引用，可移除。
  await surface2.getByRole("button", { name: "尾声", exact: true }).click();
  await surface2.getByRole("button", { name: "引用到对话" }).click();
  await expect(page.getByLabel("引用构思")).toContainText("尾声");
  await page.getByRole("button", { name: "移除构思引用 尾声", exact: true }).click();
  await expect(page.getByLabel("引用构思")).toHaveCount(0);
});

test("窄屏与大字号下构思面可读，结构不横向溢出", async ({ page, book }) => {
  const api = (path: string, init?: Parameters<ReturnType<typeof page.request.get>>[1]) =>
    page.request.fetch(`http://127.0.0.1:8931/api/file-workspaces/${book.id}${path}`, init);
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

  const output = resolve("../data/test-tmp/story-structure");
  mkdirSync(output, { recursive: true });
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  await expect(surface.getByRole("button", { name: "对质" })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("combobox", { name: "调整字体大小" }).selectOption("150");
  await page.keyboard.press("Escape");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  // 详情在窄屏下移到图下方：点开节点后仍能读到安排与操作按钮。
  await surface.getByRole("button", { name: "对质" }).click();
  await expect(surface.getByRole("heading", { name: "对质" })).toBeVisible();
  await expect(surface.getByRole("button", { name: "引用到对话" })).toBeInViewport();
  await page.screenshot({ path: join(output, "story-390-150.png") });
});
