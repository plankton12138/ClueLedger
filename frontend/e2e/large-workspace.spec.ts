import { test, expect, type Response } from "@playwright/test";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

/**
 * A22：200 节点 / 300 关系的随章首屏。
 *
 * 只验证可判定的事实：首屏是一次聚合请求，没有逐节点详情请求；分页提示存在；
 * 320px 与 720px（1440 窗口 200% 缩放的等效 CSS 视口）不产生横向溢出。
 * 耗时和响应体只作本次样本记录，不当作性能承诺。
 */

const parent = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "large-books");

function operations(): Record<string, unknown>[] {
  const ops: Record<string, unknown>[] = [];
  for (let index = 1; index <= 200; index += 1) {
    ops.push({
      op: "add_node",
      node: {
        id: `n${index}`,
        title: `节点${index}`,
        action: `第${index}个节点`,
        planned_chapter_number: 1,
      },
    });
  }
  for (let index = 1; index < 200; index += 1) {
    ops.push({
      op: "connect",
      edge: { id: `e${index}`, from_id: `n${index}`, to_id: `n${index + 1}`, kind: "next" },
    });
  }
  // 101 条重新解释关系补足 300 条；reframe 不参与推进顺序无环校验。
  for (let index = 0; index <= 100; index += 1) {
    ops.push({
      op: "connect",
      edge: {
        id: `r${index}`,
        from_id: `n${200 - index}`,
        to_id: `n${1 + index}`,
        kind: "reframe",
      },
    });
  }
  return ops;
}

test("200 节点 / 300 关系首屏只发一次聚合请求", async ({ page, request }, info) => {
  test.setTimeout(120_000);
  mkdirSync(parent, { recursive: true });
  const root = mkdtempSync(join(parent, "large-"));
  const created = await request.post("/api/file-workspaces", {
    data: { path: join(root, "大作品"), create: true },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const book = await created.json();
  writeFileSync(
    join(book.path, "正文", "0001 雨夜.md"),
    "# 雨夜\n\n" + "林砚走进旅馆，柜台上有一双湿手套，手套还在滴水。\n\n".repeat(20),
    "utf8",
  );
  const seeded = await request.post(`/api/file-workspaces/${book.id}/story/changes`, {
    data: {
      variant_id: "main",
      expected_revision: "empty",
      request_key: `large-workspace-${book.id}`,
      operations: operations(),
    },
  });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();

  const chapterResponses: Response[] = [];
  const detailRequests: string[] = [];
  page.on("response", (response) => {
    if (new URL(response.url()).pathname.endsWith("/story/chapter-context")) {
      chapterResponses.push(response);
    }
  });
  page.on("request", (requestRow) => {
    const path = new URL(requestRow.url()).pathname;
    if (path.includes("/story/variants/") && path.includes("/nodes/")) detailRequests.push(path);
  });

  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: /第一章 雨夜/ }).click();
  const panel = page.getByRole("region", { name: "本章相关" });
  const started = Date.now();
  await page.getByRole("button", { name: "本章", exact: true }).click();
  await expect(panel.getByRole("heading", { name: "本章安排" })).toBeVisible();
  const elapsed = Date.now() - started;

  await expect.poll(() => chapterResponses.length).toBe(1);
  expect(detailRequests).toEqual([]);
  await expect(panel.getByRole("button", { name: "加载更多" })).toBeVisible();
  await expect(panel.getByText("已分页显示，未一次列出全部条目。")).toBeVisible();
  const body = await chapterResponses[0].body();
  const view = JSON.parse(body.toString("utf8")) as { items: unknown[]; truncated: boolean };
  const measured = {
    elapsed_ms: elapsed,
    chapter_context_requests: chapterResponses.length,
    node_detail_requests: detailRequests.length,
    response_bytes: body.byteLength,
    items_on_first_page: view.items.length,
    truncated: view.truncated,
  };
  console.log("A22", JSON.stringify(measured));

  await info.attach("a22-first-screen.json", {
    body: JSON.stringify(measured, null, 2),
    contentType: "application/json",
  });

  for (const width of [320, 720]) {
    await page.setViewportSize({ width, height: 800 });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
      `${width}px 宽度出现横向溢出`,
    ).toBe(true);
  }
});
