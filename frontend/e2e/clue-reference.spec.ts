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

test("线索详情的讨论调整只准备焦点，不写草稿引用", async ({ page, book }) => {
  const pane = await openClueDetail(page, book);
  const posts: string[] = [];
  page.on("request", request => {
    if (request.method() === "POST" && request.url().includes("/conversations")) {
      posts.push(request.url());
    }
  });
  await pane.getByRole("button", { name: "讨论调整", exact: true }).click();

  await expect(page.locator('[aria-label="本轮讨论焦点"]')).toContainText("线索：公开的照片");
  await expect(page.locator('.chat-input')).toHaveValue("");
  expect(posts).toEqual([]);
  const shots = resolve(process.env.HASTINGS_E2E_SCREENSHOT_DIR ?? "../dev-notes/v1.0/artifacts/s6");
  mkdirSync(shots, { recursive: true });
  await page.screenshot({ path: join(shots, "clue-reference-1440.png") });

  // 焦点是未发送的引用：刷新后不保留；线索本体不受影响。
  await page.reload();
  await expect(page.locator('[aria-label="本轮讨论焦点"]')).toHaveCount(0);
  const list = page.getByRole("region", { name: "线索列表" });
  await expect(list.getByRole("button", { name: /公开的照片/ })).toBeVisible();
});

test("焦点不会被写成本地草稿引用", async ({ page, book, request }) => {
  const pane = await openClueDetail(page, book);
  await pane.getByRole("button", { name: "讨论调整", exact: true }).click();
  await expect(page.locator('[aria-label="本轮讨论焦点"]')).toContainText("公开的照片");
  await expect
    .poll(async () => {
      const ui = await request
        .get(`/api/file-workspaces/${book.id}/conversation-ui`)
        .then(response => response.json());
      const references = Object.values(
        ui.draft_contexts as Record<string, { clue_references?: Record<string, unknown>[] }>,
      ).flatMap(context => context.clue_references ?? []);
      return references.length;
    })
    .toBe(0);
});
