import { test, expect } from "./fixtures/frontendExperience";
import { writeFileSync } from "node:fs";
import { join } from "node:path";

/**
 * 正文随章查看：当前章实际出现、明确计划和失效引用分开显示；对象详情与讨论焦点
 * 共用同一辅助区。数据由真实服务读取，不调用模型。
 */

const CHAPTER1 = "正文/0001 雨夜.md";
const CHAPTER2 = "正文/0002 照片.md";

function key(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function spanOf(text: string, quote: string): { start: number; end: number } {
  const utf16 = text.indexOf(quote);
  expect(utf16).toBeGreaterThanOrEqual(0);
  const start = Array.from(text.slice(0, utf16)).length;
  return { start, end: start + Array.from(quote).length };
}

test("本章相关按出现、计划和失效分组，对象与讨论共用辅助区", async ({ page, book }) => {
  writeFileSync(join(book.path, CHAPTER2), "# 照片\n\n他们找到了那张旧照片。\n", "utf8");
  const first = await page.request
    .get(`/api/file-workspaces/${book.id}/file?path=${encodeURIComponent(CHAPTER1)}`)
    .then(response => response.json());
  const second = await page.request
    .get(`/api/file-workspaces/${book.id}/file?path=${encodeURIComponent(CHAPTER2)}`)
    .then(response => response.json());
  const quote1 = "原文新增了一份潮汐表。";
  const quote2 = "他们找到了那张旧照片。";
  const span1 = spanOf(first.text as string, quote1);
  const span2 = spanOf(second.text as string, quote2);

  const seeded = await page.request.post(`/api/file-workspaces/${book.id}/story/changes`, {
    data: {
      variant_id: "main",
      expected_revision: "empty",
      request_key: key("chapter-context"),
      operations: [
        { op: "create_clue_identity", temp_id: "tide", title: "潮汐表" },
        { op: "upsert_clue_plan", clue_id: "tide", plan: { kind: "truth" } },
        {
          op: "add_node",
          node: { id: "n1", title: "抵达", action: "侦探看见潮汐表" },
        },
        {
          op: "add_node",
          node: {
            id: "n2",
            title: "旧信",
            action: "信件被找出",
            planned_chapter_number: 1,
          },
        },
        {
          op: "add_node",
          node: { id: "n3", title: "照片", action: "旧照片被找到" },
        },
        {
          op: "bind_prose",
          binding: {
            id: "b1",
            node_id: "n1",
            path: CHAPTER1,
            digest: first.digest,
            start: span1.start,
            end: span1.end,
            quote: quote1,
          },
        },
        {
          op: "bind_prose",
          binding: {
            id: "b2",
            clue_id: "tide",
            path: CHAPTER1,
            digest: first.digest,
            start: span1.start,
            end: span1.end,
            quote: quote1,
          },
        },
        {
          op: "bind_prose",
          binding: {
            id: "b3",
            node_id: "n3",
            path: CHAPTER2,
            digest: second.digest,
            start: span2.start,
            end: span2.end,
            quote: quote2,
          },
        },
      ],
    },
  });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
  // 关联登记后再删掉原句：这一章出现的是失效引用，而不是猜测位置。
  writeFileSync(join(book.path, CHAPTER2), "# 照片\n\n另一段内容。\n", "utf8");

  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: /第一章 雨夜/ }).click();
  await page.getByRole("button", { name: "本章", exact: true }).click();

  const panel = page.getByRole("region", { name: "本章相关" });
  await expect(panel).toContainText("正文出现");
  await expect(panel).toContainText("抵达");
  await expect(panel).toContainText("潮汐表");
  await expect(panel).toContainText("本章安排");
  await expect(panel).toContainText("旧信");
  await expect(panel.getByText("引用失效")).toHaveCount(0);

  await panel.getByRole("button", { name: /抵达/ }).click();
  const detail = page.getByRole("region", { name: "详情" });
  await expect(detail.getByRole("heading", { name: "抵达" })).toBeVisible();
  await expect(detail.getByText(quote1)).toBeVisible();
  await expect(page).toHaveURL(/mode=chapter/);
  await expect(page).toHaveURL(/node=n1/);
  await page.getByRole("button", { name: "关闭详情", exact: true }).click();
  await expect(page.getByRole("region", { name: "本章相关" })).toBeVisible();
  await expect(page).toHaveURL(/mode=chapter/);

  await page.getByRole("button", { name: "讨论本章", exact: true }).click();
  await expect(page.getByLabel("本轮讨论焦点")).toContainText("本章：第一章 雨夜");
  await page.getByRole("button", { name: "返回详情", exact: true }).click();

  await page.getByRole("button", { name: /第二章 照片/ }).click();
  const next = page.getByRole("region", { name: "本章相关" });
  await expect(next).toContainText("引用失效");
  await expect(next).toContainText("照片");
  await expect(next).toContainText("原句已不在正文");
  await expect(next).not.toContainText("本章安排");
});
