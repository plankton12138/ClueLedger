import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

import type { Locator, Page } from "@playwright/test";

import { test, expect } from "./fixtures/frontendExperience";

const CHAPTER = "正文/0001 雨夜.md";
const QUOTE = "侦探抵达港口，雨仍未停。";

function requestKey(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

async function selectText(root: Locator, text: string) {
  await root.evaluate((node, wanted) => {
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    let current: Node | null;
    while ((current = walker.nextNode())) {
      const start = current.textContent?.indexOf(wanted) ?? -1;
      if (start < 0) continue;
      const range = document.createRange();
      range.setStart(current, start);
      range.setEnd(current, start + wanted.length);
      window.getSelection()?.removeAllRanges();
      window.getSelection()?.addRange(range);
      document.dispatchEvent(new Event("selectionchange"));
      return;
    }
    throw new Error(`没有找到选段：${wanted}`);
  }, text);
}

async function seedNodeWithBinding(page: Page, workspaceId: string, action = "木板没有响") {
  // 夹具会往章节追加一段原文，摘要以服务端读到的当前正文为准。
  const view = await page.request
    .get(`/api/file-workspaces/${workspaceId}/file?path=${encodeURIComponent(CHAPTER)}`)
    .then(response => response.json());
  const digest = view.digest as string;
  const start = Array.from((view.text as string).slice(0, (view.text as string).indexOf(QUOTE))).length;
  const node = await page.request.post(`/api/file-workspaces/${workspaceId}/story/changes`, {
    data: {
      variant_id: "main",
      expected_revision: "empty",
      request_key: requestKey("seed-node"),
      operations: [
        {
          op: "add_node",
          node: { id: "n1", title: "发现异样", action, planned_chapter_number: 1 },
        },
      ],
    },
  });
  expect(node.ok(), await node.text()).toBeTruthy();
  // 第二次提交时方案已存在，服务端会按当前构思写入核对基准。
  const variant = await page.request
    .get(`/api/file-workspaces/${workspaceId}/story/variants/main`)
    .then(response => response.json());
  const seeded = await page.request.post(`/api/file-workspaces/${workspaceId}/story/changes`, {
    data: {
      variant_id: "main",
      expected_revision: variant.revision,
      request_key: requestKey("seed-binding"),
      operations: [
        {
          op: "bind_prose",
          binding: {
            id: "b1",
            node_id: "n1",
            document_id: "",
            path: CHAPTER,
            digest,
            start,
            end: start + Array.from(QUOTE).length,
            quote: QUOTE,
          },
        },
      ],
    },
  });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
}

test("正文选段登记真相线索，刷新后仍显示在同一区间", async ({ page, book }) => {
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: "第一章 雨夜", exact: true }).click();
  const body = page.locator(".paper-sheet [data-text-selection-source]");
  await expect(body).toContainText(QUOTE);
  await selectText(body, QUOTE);
  await page.getByRole("button", { name: "标记线索", exact: true }).click();

  const panel = page.getByRole("dialog", { name: "登记线索" });
  await panel.getByLabel("线索名称").fill("雨夜里的抵达");
  await panel.getByRole("button", { name: "真相线索", exact: true }).click();
  await panel.getByRole("button", { name: "登记", exact: true }).click();
  await expect(panel).toHaveCount(0);

  const mark = page.locator(".prose-paper .clue-mark").first();
  await expect(mark).toBeVisible();
  await expect(mark).toHaveClass(/truth/);
  await page.reload();
  await page.getByRole("button", { name: "第一章 雨夜", exact: true }).click();
  await expect(page.locator(".prose-paper .clue-mark").first()).toHaveClass(/truth/);

  await page.getByRole("link", { name: "线索", exact: true }).click();
  const list = page.getByRole("region", { name: "线索列表" });
  await expect(list.getByRole("button", { name: /雨夜里的抵达/ })).toBeVisible();
  await expect(list.getByText("真相线索")).toBeVisible();
  const output = resolve("../data/test-tmp/clueledger-workspace");
  mkdirSync(output, { recursive: true });
  await page.screenshot({ path: resolve(output, "clue-page-1440.png") });
});

test("改构思后出现需核对，稍后处理只隐藏提醒", async ({ page, book }) => {
  await seedNodeWithBinding(page, book.id);
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("link", { name: "构思", exact: true }).click();
  const surface = page.getByRole("region", { name: "构思结构" });
  await surface.getByRole("button", { name: /发现异样/ }).click();

  const detail = page.getByRole("region", { name: "详情" });
  await expect(detail.getByText("就绪")).toBeVisible();
  await detail.getByLabel("构思内容").fill("侦探确认有人走过");
  await detail.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(detail.getByText(/需核对/)).toBeVisible();
  await expect(detail.getByRole("button", { name: "稍后处理", exact: true })).toBeVisible();

  await detail.getByRole("button", { name: "稍后处理", exact: true }).click();
  await expect(detail.getByRole("button", { name: "稍后处理", exact: true })).toHaveCount(0);
  await expect(detail.getByText(/需核对/)).toBeVisible();

  await page.reload();
  await page.getByRole("link", { name: "构思", exact: true }).click();
  await page.getByRole("region", { name: "构思结构" }).getByRole("button", { name: /发现异样/ }).click();
  await expect(detail.getByText(/需核对/)).toBeVisible();
  await expect(detail.getByRole("button", { name: "稍后处理", exact: true })).toHaveCount(0);
});

test("线索关系在同一详情区打开，列表与图用同一份数据", async ({ page, book }) => {
  const created = await page.request.post(`/api/file-workspaces/${book.id}/story/changes`, {
    data: {
      variant_id: "main",
      expected_revision: "empty",
      request_key: requestKey("clues"),
      operations: [
        { op: "create_clue_identity", temp_id: "clock", title: "停住的座钟" },
        { op: "create_clue_identity", temp_id: "ticket", title: "船票" },
        { op: "upsert_clue_plan", clue_id: "clock", plan: { kind: "truth" } },
        { op: "upsert_clue_plan", clue_id: "ticket", plan: { kind: "mislead" } },
      ],
    },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const idMap = (await created.json()).id_map as Record<string, string>;
  const revision = await page.request
    .get(`/api/file-workspaces/${book.id}/story/variants/main`)
    .then(response => response.json());
  const related = await page.request.post(`/api/file-workspaces/${book.id}/story/changes`, {
    data: {
      variant_id: "main",
      expected_revision: revision.revision,
      request_key: requestKey("relation"),
      operations: [
        {
          op: "upsert_clue_relation",
          relation: {
            id: "r1",
            from_clue_id: idMap.clock,
            to_clue_id: idMap.ticket,
            kind: "supports",
            reason: "停钟与船票登记时间互相印证",
          },
        },
      ],
    },
  });
  expect(related.ok(), await related.text()).toBeTruthy();

  await page.goto(`/#/workspaces/${book.id}/writing?mode=clues`);
  const list = page.getByRole("region", { name: "线索列表" });
  await list.getByRole("button", { name: /停住的座钟/ }).click();
  const relations = page.getByRole("region", { name: "线索关系" });
  const item = relations.getByRole("button", { name: /支撑.*船票/ });
  await expect(item).toBeVisible();
  await item.click();
  const detail = page.getByRole("region", { name: "详情" });
  await expect(detail.getByRole("heading", { name: "船票" })).toBeVisible();
  await expect(detail.getByRole("button", { name: "误导线索" })).toHaveAttribute("aria-pressed", "true");
});
