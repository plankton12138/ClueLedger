import { expect, test } from "@playwright/test";

/**
 * J1 冒烟（方案 §5 P0）：`/` 静态托管生效 → 品牌区渲染 → WS 连接徽章。
 * driver 预建 e2e-home 工作区，bootWorkspaces 按字典序自动选中 → /ws/e2e-home 连接。
 * 断言读事实源文案（getByRole/中文可访问名），零 waitForTimeout。
 */
test("J1 冒烟：首页加载 → 健康连接", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("黑斯廷斯备忘录")).toBeVisible();
  await expect(page.getByText("已连接")).toBeVisible();
});
