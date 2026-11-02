import { test, expect } from "./fixtures/frontendExperience";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

/** 没有构思时账本线索仍可读、可讨论，但阅读不会自动建立主方案。 */
test.beforeEach(async ({ book }) => {
  const root = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e");
  execFileSync(
    "uv",
    [
      "run",
      "--directory",
      "..",
      "python",
      resolve("e2e/fixtures/clue_seed.py"),
      book.path,
      resolve(root, "state", "workspaces", book.id),
    ],
    { windowsHide: true, stdio: "pipe" },
  );
});

test("没有构思时线索列表与详情仍可用，讨论不建立主方案", async ({ page, book }) => {
  await page.goto(`/#/workspaces/${book.id}/writing?mode=clues`);
  const list = page.getByRole("region", { name: "线索列表" });
  await expect(list.getByRole("button", { name: /公开的照片/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "查看关系图" })).toBeDisabled();
  await list.getByRole("button", { name: /公开的照片/ }).click();

  const detail = page.getByRole("region", { name: "详情" });
  await expect(detail.getByRole("heading", { name: "公开的照片" })).toBeVisible();
  await expect(detail.getByText("照片证明她看过背面。")).toBeVisible();
  await detail.getByRole("button", { name: "讨论调整", exact: true }).click();
  await expect(page.getByLabel("本轮讨论焦点")).toContainText("线索：公开的照片");

  const variants = await page.request.get(`/api/file-workspaces/${book.id}/story/variants`);
  expect((await variants.json()).variants).toEqual([]);
});
