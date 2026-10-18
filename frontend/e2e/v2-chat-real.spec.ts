import { expect, test, type Page } from "@playwright/test";

test("真实桌面默认显示对话栏，升级时忽略旧自动收起记录，主动收起仍可恢复", async ({ page }, info) => {
  await page.addInitScript(() => localStorage.setItem("hastings.chat.open", "0"));
  await project(page, "默认常驻对话");
  const sidebar = page.getByRole("complementary", { name: "对话栏", exact: true });
  await expect(sidebar).toBeVisible();
  await page.getByRole("navigation", { name: "主导航" })
    .getByRole("button", { name: "创作台", exact: true }).click();
  await expect(sidebar).toBeVisible();
  await page.screenshot({ path: info.outputPath("desktop-chat-default.png") });
  await sidebar.getByRole("button", { name: "收起对话栏", exact: true }).click();
  await expect(sidebar).toHaveCount(0);
  await page.reload();
  await expect(sidebar).toHaveCount(0);
  await page.getByRole("button", { name: "显示对话栏", exact: true }).click();
  await expect(sidebar).toBeVisible();
});

test("真实窄屏首次打开不遮挡正文，可经对话按钮打开", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const panel = page.getByRole("complementary", { name: "对话面板", exact: true });
  await expect(panel).toHaveCount(0);
  await page.getByRole("button", { name: "显示对话栏", exact: true }).click();
  await expect(panel).toBeVisible();
});

async function project(page: Page, name: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("textbox", { name: "新故事名称" }).fill(name);
  await page.getByRole("button", { name: "新建故事", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "对话输入" })).toBeVisible();
  await expect(page.locator(".chat-loading")).toHaveCount(0);
  return (await page.evaluate(() => localStorage.getItem("hastings.v2.current")))!;
}

test("真实对话：完成前可见分片，停止后保留且不创建提交或任务", async ({ page, request }) => {
  const pid = await project(page, "真实流式");
  await page.getByRole("textbox", { name: "对话输入" }).fill("讨论人物动机");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("第一段真实回复", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "停止回复" })).toBeVisible();
  await page.getByRole("button", { name: "停止回复" }).click();
  await expect(page.getByRole("button", { name: "发送", exact: true })).toBeVisible();
  const manuscript = await (await request.get(`/api/v2/projects/${pid}/manuscript`)).json();
  const jobs = await (await request.get(`/api/v2/projects/${pid}/agent-jobs`)).json();
  expect(manuscript.commit_id).toBeNull();
  expect(jobs.jobs).toEqual([]);
  await page.reload();
  await expect(page.getByText("第一段真实回复", { exact: true })).toBeVisible();
});

test("真实任务：缺失项正常显示，空项目规划、正文候选均可在主区审阅后提交", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await project(page, "真实创作闭环");
  await page.getByRole("button", { name: "✎ 创作操作", exact: true }).click();
  await page.getByRole("button", { name: /生成章节草稿/ }).click();
  await expect(page.getByLabel("缺失项")).toHaveCount(0);
  await page.getByRole("button", { name: "放弃", exact: true }).click();
  await page.getByRole("textbox", { name: "对话输入" }).fill("规划怀表谜案");
  await page.getByRole("button", { name: "✎ 创作操作", exact: true }).click();
  await page.getByRole("button", { name: /规划故事与节拍/ }).click();
  await page.getByRole("button", { name: "开始（创建创作任务）", exact: true }).click();
  await expect(page.getByRole("article", { name: "执行任务", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "打开审阅", exact: true }).click();
  await expect(page.getByLabel("候选完整内容")).toContainText("林岚");
  await page.getByLabel("候选完整内容").getByRole("button", { name: "接受候选并提交", exact: true }).click();
  await expect(page.getByLabel("候选完整内容").getByRole("button", { name: "已接受", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "✎ 创作操作", exact: true }).click();
  await page.getByRole("button", { name: /生成章节草稿/ }).click();
  await page.getByRole("button", { name: "开始（创建创作任务）", exact: true }).click();
  await expect(page.getByRole("article", { name: "待审阅候选", exact: true })).toHaveCount(2);
  await page.getByRole("button", { name: "打开审阅", exact: true }).last().click();
  await expect(page.getByLabel("候选完整内容")).toContainText("林岚非常缓慢地递出怀表。");
  await page.getByLabel("候选完整内容").getByRole("button", { name: "接受候选并提交", exact: true }).click();
  await expect(page.getByLabel("候选完整内容").getByRole("button", { name: "已接受", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "创作台", exact: true }).click();
  const chapters = page.locator("button.asset-row");
  await chapters.first().click();
  const editor = page.getByRole("textbox", { name: "人工正文" });
  await expect(editor).toBeVisible();
  await page.getByRole("button", { name: "✎ 创作操作", exact: true }).click();
  await page.getByRole("button", { name: /生成章节草稿/ }).click();
  await page.getByRole("button", { name: "开始（创建创作任务）", exact: true }).click();
  await expect(page.getByRole("article", { name: "待审阅候选", exact: true })).toHaveCount(3);
  await editor.fill("作者尚未保存的正文");
  await page.getByRole("button", { name: "打开审阅", exact: true }).last().click();
  await expect(page.getByLabel("候选完整内容")).toContainText("当前正文有未保存稿");
  await expect(page.getByLabel("候选完整内容").getByRole("button", { name: "接受候选并提交", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "创作台", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "人工正文" })).toHaveValue("作者尚未保存的正文");
  expect(errors).toEqual([]);
});
