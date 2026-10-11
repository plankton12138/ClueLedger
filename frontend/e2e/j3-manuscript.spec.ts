import { expect, test } from "@playwright/test";

/**
 * J3 P1 旅程（方案 §5）：正文三态——阅读 / 编辑 / 对账。
 * 打开预置的 e2e-writing（stage=writing + ch01 正文），断言：
 * 羊皮纸渲染预置正文 → 编辑态改字出「未保存」徽标 → 保存 → 对账态 diff 色块。
 */
test("J3 正文三态：阅读/编辑/对账", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "文章", exact: true }).click();
  const row = page.locator("li", { hasText: "e2e-writing" });
  await row.getByRole("button", { name: "进入" }).click();

  // 阅读态：章节自动选中第一章，羊皮纸渲染预置正文
  await expect(page.getByLabel("章节")).toContainText("第1章");
  await expect(page.getByText("雨下了一整夜。")).toBeVisible();

  // 编辑态：改字 → dirty 徽标 → 保存（编辑框是含标题行 # 第1章 的整文件，
  // 受管写端点 validate_title_heading 强制标题行——fill 须保留）
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = page.getByLabel("正文编辑");
  await expect(editor).toBeVisible();
  await editor.fill(
    "# 第1章 雨夜的客人\n" +
      "\n" +
      "雨下了一整夜。艾琳把最后一间客房的钥匙推过柜台时，注意到那位客人的\n" +
      "大衣口袋里露出一截怀表链——但三日前失踪的维恩，随身那只从来不用表链。\n" +
      "她没有作声，只是把登记簿悄悄翻回了上一页。",
  );
  await expect(page.getByText("● 未保存")).toBeVisible();
  await page.getByRole("button", { name: "保存 ⌘S" }).click();
  await expect(page.getByText("● 未保存")).toHaveCount(0);

  // 对账态：保存响应携带 diff → 对账 Tab 亮起 → 段落级 diff 色块渲染
  const diffTab = page.getByRole("button", { name: "对账", exact: true });
  await expect(diffTab).toBeEnabled();
  await diffTab.click();
  await expect(page.getByText(/对账视图：\d+ 处段落变更/)).toBeVisible();
  await expect(page.getByText(/但三日前失踪的维恩，随身那只从来不用表链/)).toBeVisible();
});
