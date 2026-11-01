import { test, expect } from "./fixtures/frontendExperience";
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { join, resolve } from "node:path";

test.beforeEach(async ({ book }) => {
  const root = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e");
  execFileSync("uv", ["run", "--directory", "..", "python", resolve("e2e/fixtures/clue_seed.py"), book.path, resolve(root, "state", "workspaces", book.id)], { windowsHide: true, stdio: "pipe" });
  execFileSync("uv", ["run", "--directory", "..", "python", resolve("e2e/fixtures/story_variant_seed.py"), resolve(root, "state"), book.id], { windowsHide: true, stdio: "pipe" });
});

async function openClueDetail(page: import("@playwright/test").Page, book: { id: string }) {
  // 线索入口移到主导航；详情与列表分在同一页。
  await page.goto(`/#/workspaces/${book.id}/writing?mode=clues`);
  const list = page.getByRole("region", { name: "线索列表" });
  await list.getByRole("button", { name: /公开的照片/ }).click();
  return page.getByRole("region", { name: "详情" });
}

test("把线索带进对话只写草稿，刷新后仍在，可移除", async ({ page, book }) => {
  const pane = await openClueDetail(page, book);
  await pane.getByRole("button", { name: "带入对话", exact: true }).click();

  const clues = page.locator('[aria-label="引用线索"]');
  await expect(clues).toContainText("公开的照片");
  await expect(page.locator(".chat-input")).toHaveValue("");
  const shots = resolve("../dev-notes/v1.0/artifacts/s6");
  mkdirSync(shots, { recursive: true });
  await page.screenshot({ path: join(shots, "clue-reference-1440.png") });

  await page.reload();
  await expect(page.locator('[aria-label="引用线索"]')).toContainText("公开的照片");

  await page.getByRole("button", { name: "移除引用 公开的照片", exact: true }).click();
  await expect(page.locator('[aria-label="引用线索"]')).toHaveCount(0);
});

test("草稿里的线索引用只保存标识与阅读版本", async ({ page, book, request }) => {
  const pane = await openClueDetail(page, book);
  await pane.getByRole("button", { name: "带入对话", exact: true }).click();
  await expect(page.locator('[aria-label="引用线索"]')).toContainText("公开的照片");
  await expect
    .poll(async () => {
      const ui = await request
        .get(`/api/file-workspaces/${book.id}/conversation-ui`)
        .then(response => response.json());
      const references = Object.values(
        ui.draft_contexts as Record<string, { clue_references?: Record<string, unknown>[] }>,
      ).flatMap(context => context.clue_references ?? []);
      return references[0] ? Object.keys(references[0]).sort() : [];
    })
    .toEqual(["browse_as_of_chapter", "clue_id", "clue_revision", "title", "workspace_lineage"]);
});
