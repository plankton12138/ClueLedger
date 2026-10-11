import { expect, test } from "@playwright/test";

/**
 * J5 P1 旅程（方案 §5）：删文章确认守门。
 * 删除 AlertDialog：确认键在输入不匹配时禁用 → 输对 slug 亮键 → 确认后列表行消失。
 * 删除对象用预置的 e2e-home（空工作区，无其他旅程依赖其存在）。
 */
test("J5 删文章确认守门", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "文章", exact: true }).click();

  const row = page.locator("li", { hasText: "e2e-home" });
  await row.getByRole("button", { name: "删除" }).click();

  // 守门：确认键禁用（slug 不匹配），误输也不亮
  const action = page.getByRole("button", { name: "确认删除" });
  await expect(page.getByText("删除文章「e2e-home」？")).toBeVisible();
  await expect(action).toBeDisabled();
  await page.getByPlaceholder("e2e-home").fill("e2e-hom");
  await expect(action).toBeDisabled();

  // 输对亮键 → 确认 → 文章从列表消失
  await page.getByPlaceholder("e2e-home").fill("e2e-home");
  await expect(action).toBeEnabled();
  await action.click();
  await expect(row).toHaveCount(0);
});
