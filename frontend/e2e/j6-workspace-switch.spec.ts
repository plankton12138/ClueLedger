import { expect, test } from "@playwright/test";

/**
 * J6 P1 旅程（方案 §5）：多工作区切换。
 * 顶栏徽标随 slug 变；选中经 ?w= 记入地址；page.reload() 后恢复选择
 * （loadSnapshot 教训的守门：刷新后不丢工作区、不重复渲染）。
 */
test("J6 多工作区切换与地址恢复", async ({ page }) => {
  await page.goto("/");
  // 启动默认选中排序首位（非 e2e-writing），经「文章」页切到预置写作工作区
  await page.getByRole("button", { name: "文章", exact: true }).click();
  const row = page.locator("li", { hasText: "e2e-writing" });
  await row.getByRole("button", { name: "进入" }).click();

  // 顶栏徽标随 slug 变；?w= 记入地址
  const badge = page.getByRole("button", { name: "e2e-writing", exact: true });
  await expect(badge).toBeVisible();
  expect(new URL(page.url()).searchParams.get("w")).toBe("e2e-writing");

  // 刷新恢复选择：?w= 优先，快照重放不重复
  await page.reload();
  await expect(page.getByRole("button", { name: "e2e-writing", exact: true })).toBeVisible();
  await expect(page.getByText("已连接")).toBeVisible();
});
