import { expect, test, type Page } from "@playwright/test";

/**
 * V03-3.2 写作台账本走查（真实 API + SQLite）：
 * 直接保存 → 回读真实版本 → 暂存候选 → 假设不入账 → 部分确认 → 修订。
 */

async function project(page: Page, name: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("textbox", { name: "新故事名称" }).fill(name);
  await page.getByRole("button", { name: "新建故事", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("navigation", { name: "主导航" })
    .getByRole("button", { name: "写作台", exact: true }).click();
  await expect(page.locator('section[aria-label="故事账本"]')).toBeVisible();
}

test("写作台账本：直接保存回读版本，候选暂存与部分确认，假设不入账", async ({ page }, info) => {
  await project(page, "账本走查");
  const ledger = page.locator('section[aria-label="故事账本"]');
  const pending = page.locator('section[aria-label="待确认设定"]');

  // 1. 直接保存一个人物，回读真实版本。
  await ledger.getByRole("button", { name: "新增设定" }).click();
  let dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "新增设定" })).toBeVisible();
  await dialog.getByLabel(/姓名/).fill("姐姐");
  await dialog.getByLabel(/身份/).fill("旅馆经营者");
  await dialog.getByRole("button", { name: "保存设定" }).click();
  await expect(page.getByText(/已保存「姐姐」/)).toBeVisible();
  await expect(page.getByText(/版本 cmt_[0-9a-f]{4}/)).toBeVisible();
  await expect(ledger.getByRole("button", { name: /姐姐/ })).toBeVisible();
  await expect(ledger.getByText(/旅馆经营者/)).toBeVisible();
  await page.screenshot({ path: info.outputPath("ledger-direct-save.png") });

  // 2. 暂存一个「假设」：可以在候选区看到，但勾选被禁用、不会入账。
  await ledger.getByRole("button", { name: "新增设定" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel(/姓名/).fill("管家");
  await dialog.getByLabel(/这只是假设/).check();
  await dialog.getByRole("button", { name: "存为待确认" }).click();
  await expect(pending.getByText("管家")).toBeVisible();
  await expect(pending.getByText(/假设 · 不会入账/)).toBeVisible();
  await expect(pending.getByLabel(/选择候选 管家/)).toBeDisabled();

  // 3. 暂存一个正式候选；部分确认只入账选中的条目。
  await ledger.getByRole("button", { name: "新增设定" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel(/姓名/).fill("妹妹");
  await dialog.getByRole("button", { name: "存为待确认" }).click();
  await expect(pending.getByText("妹妹")).toBeVisible();
  await expect(pending.getByText(/待确认 · 2/)).toBeVisible();

  await pending.getByLabel(/选择候选 妹妹/).check();
  await pending.getByRole("button", { name: "确认选中（1）" }).click();
  await expect(page.getByText(/已保存「1 条设定」/)).toBeVisible();
  await expect(ledger.getByRole("button", { name: /妹妹/ })).toBeVisible();
  await expect(pending.getByText("妹妹")).toHaveCount(0);
  await expect(pending.getByText("管家")).toBeVisible();
  await expect(pending.getByText(/待确认 · 1/)).toBeVisible();

  // 4. 修订正式条目：保存后回读新版本。
  await ledger.getByRole("button", { name: /姐姐/ }).click();
  dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "编辑设定" })).toBeVisible();
  await dialog.getByLabel(/目标/).fill("守住旅馆的秘密");
  await dialog.getByRole("button", { name: "保存修改" }).click();
  await expect(page.getByText(/已保存「姐姐」/)).toBeVisible();
  await expect(ledger.getByText(/守住旅馆的秘密/)).toBeVisible();
  await page.screenshot({ path: info.outputPath("ledger-version-readback.png") });
});
