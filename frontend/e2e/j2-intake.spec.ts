import { expect, test } from "@playwright/test";

/**
 * J2 P0 旅程（方案 §5）：新建文章 → 发委托 → FakeProvider intake 剧本流式渲染。
 * 断言口径（§6 反脆弱约定）：断终态事实（文案/角色/计数），零 waitForTimeout；
 * 「文章」页新建表单 → 自动切回创作台 → 发送 → 用户气泡 → bible_upsert 工具卡
 * ×2（被阶段守卫拒绝）→ 总结回合文本落定 → 验证账本未写入 → 发送按钮恢复。
 */
test("J2 新建文章 → 发委托 → intake 拒绝未确认设定", async ({ page, request }) => {
  const slug = `e2e-j2-${Date.now()}`;

  await page.goto("/");
  await page.getByRole("button", { name: "文章", exact: true }).click();
  await page.getByRole("button", { name: "＋ 新建文章" }).click();

  await page.getByLabel("故事标识").fill(slug);
  await page.getByRole("button", { name: "打开新文章" }).click();

  // 新建成功即自动切换：顶栏徽标显示新 slug，创作台输入笼可编辑
  const composer = page.getByPlaceholder("给主编下达指令…");
  await expect(composer).toBeVisible();
  await expect(page.getByRole("button", { name: slug, exact: true })).toBeVisible();

  // 发委托：用户气泡原样回显
  const brief = "这是旅馆老板娘的委托：住客接连失踪，请先登记两位相关人物。";
  await composer.fill(brief);
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText(brief)).toBeVisible();

  // 新工作区仍在 intake，未经作者确认的写入必须被拒绝。
  await expect(page.getByRole("button", { name: /bible_upsert.*失败/ })).toHaveCount(2);
  await expect(page.getByText("两位角色已建档。接下来可以安排委托场景了。")).toBeVisible();
  // Fake 的自然语言声称成功不构成落盘证据，必须核对真实账本。
  const bible = await request.get(`/api/${slug}/bible/character`);
  expect(bible.ok()).toBeTruthy();
  expect((await bible.json()).count).toBe(0);

  // run 结束：发送按钮回归（不再是「插话」），输入笼回到空闲占位
  await expect(page.getByRole("button", { name: "发送", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "插话" })).toHaveCount(0);
});
