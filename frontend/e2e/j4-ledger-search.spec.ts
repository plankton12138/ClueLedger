import { expect, test } from "@playwright/test";

/**
 * J4 P1 旅程（方案 §5）：账本与搜索跳转（预置 e2e-writing：角色 ×2 + ch01 正文）。
 * 五账本 Tabs 依次切换（空账本空态文案）→ SearchBox 搜角色名 → Popover 命中
 * → 跳转切对应 Tab + 行渲染；再搜正文关键词 → 章节命中 → 创作台定位该章。
 */
test("J4 账本切换与搜索跳转", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "文章", exact: true }).click();
  const row = page.locator("li", { hasText: "e2e-writing" });
  await row.getByRole("button", { name: "进入" }).click();

  // 五账本 Tabs 依次切换：角色账本有两条预置角色，其余空账本出空态文案
  for (const emptyTab of ["线索", "伏笔", "手法", "时间线"]) {
    await page.getByRole("tab", { name: emptyTab }).click();
    await expect(page.getByText("该账本暂无条目。")).toBeVisible();
  }
  await page.getByRole("tab", { name: "角色" }).click();
  await expect(page.getByRole("button", { name: /艾琳/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /维恩/ })).toBeVisible();

  // 搜索角色：Popover 命中 → 点击 → 创作台账本面板切到角色 Tab
  const search = page.getByLabel("全局搜索");
  await search.click();
  await search.fill("艾琳");
  await page.getByRole("button", { name: /^艾琳/ }).click();
  await expect(page.getByRole("tab", { name: "角色" })).toHaveAttribute("data-state", "active");
  await expect(page.getByRole("button", { name: /艾琳/ })).toBeVisible();

  // 搜索正文关键词：章节命中（名 = 第N章 + 标题）→ 创作台定位该章
  await search.click();
  await search.fill("怀表");
  await page.getByRole("button", { name: /^第1章/ }).click();
  await expect(page.getByText("雨下了一整夜。")).toBeVisible();
});
