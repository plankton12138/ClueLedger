import { expect, test, type Page } from "@playwright/test";

async function project(page: Page, name: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("textbox", { name: "新故事名称" }).fill(name);
  await page.getByRole("button", { name: "新建故事", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  if (await page.getByRole("button", { name: "显示对话栏", exact: true }).isVisible())
    await page.getByRole("button", { name: "显示对话栏", exact: true }).click();
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
  await expect(page.getByLabel("缺失项")).toContainText("没有已接受的节拍");
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
