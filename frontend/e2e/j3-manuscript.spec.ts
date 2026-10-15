import { expect, test } from "@playwright/test";

/**
 * J3 P1 旅程（V1 只读退役后）：正文阅读态。
 * 打开预置的 e2e-writing（stage=writing + ch01 正文），断言：
 * 羊皮纸渲染预置正文 → 头部「V1 只读」提示 → 编辑/保存/对账入口已下线
 * （正文改动走 V2 仓库项目）。
 */
test("J3 正文只读：阅读态完整、写入口下线", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "文章", exact: true }).click();
  const row = page.locator("li", { hasText: "e2e-writing" });
  await row.getByRole("button", { name: "进入" }).click();

  // 阅读态：章节自动选中第一章，羊皮纸渲染预置正文
  await expect(page.getByLabel("章节")).toContainText("第1章");
  await expect(page.getByText("雨下了一整夜。")).toBeVisible();

  // 只读提示在头部；编辑/保存入口不再出现
  await expect(page.getByText("V1 只读 · 改写请迁移到 V2 项目")).toBeVisible();
  await expect(page.getByRole("button", { name: "编辑", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "保存 ⌘S" })).toHaveCount(0);
});
