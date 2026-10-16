/**
 * Phase 19.1 对话栏走查（v2-chat）：可执行的场景清单。
 *
 * 对照 dev-notes/v2/plans/phase-19-chat-workbench-plan.md §19.1 退出条件：
 * 1. 从构想到候选审阅可以在前端完整走通（模拟器演示，?chatMock=1 入口）；
 * 2. 切七页、开关侧栏、输入法发送、滚动、窄屏都能使用；
 * 3. 作者能从文案区分聊天、任务和正式内容；
 * 4. 每个按钮的前置条件、状态和错误处理明确。
 *
 * 模拟模式只在显式 `?chatMock=1` 入口启用，界面常驻「模拟模式」标识；
 * 不带该入口时验证旧后端能力提示（A20），不展示假空白或虚假成功。
 */

import { expect, test, type Page } from "@playwright/test";

const MOCK = "/?chatMock=1";

async function createProject(
  page: Page,
  name: string,
  mock = true,
): Promise<string> {
  await page.goto(mock ? MOCK : "/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("textbox", { name: "新故事名称" }).fill(name);
  await page.getByRole("button", { name: "新建故事", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  return (await page.evaluate(() =>
    localStorage.getItem("hastings.v2.current"),
  ))!;
}

async function send(page: Page, text: string) {
  const input = page.getByRole("textbox", { name: "对话输入" });
  await input.fill(text);
  await page.getByRole("button", { name: "发送", exact: true }).click();
}

test("19.1 七页常驻对话栏：切页不销毁会话，侧栏可收起，窄屏覆盖面板", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await createProject(page, "雾港对话");

  const sidebar = page.getByRole("complementary", { name: "对话栏" });
  await expect(sidebar).toBeVisible();
  await expect(sidebar.getByText("模拟模式", { exact: false })).toBeVisible();
  await expect(page.getByTestId("chat-status")).toHaveText(/模拟模式/);

  // 场景 1：普通讨论只对话——流式增量回复，不产生任何任务卡。
  await send(page, "讨论角色动机：她为什么藏起怀表");
  const assistant = sidebar.getByRole("article", { name: "助手消息" });
  await expect(assistant).toBeVisible();
  // 完成前即可见到部分文本（流式，不是等完整回复才出现）。
  await expect(assistant.getByText(/悬念不来自隐瞒信息/)).toBeVisible({ timeout: 8000 });
  await expect(sidebar.getByRole("article", { name: "任务建议" })).toHaveCount(0);
  await expect(sidebar.getByRole("article", { name: "作者消息" })).toHaveCount(1);
  await page.screenshot({ path: info.outputPath("01-chat-discussion.png") });

  // 场景 2：切七页会话不销毁、草稿保留。
  await sidebar.getByRole("textbox", { name: "对话输入" }).fill("切页前的草稿");
  for (const label of ["创作台", "故事账本", "变更影响", "修订审阅", "悬疑审计", "作者模型", "项目概览"]) {
    await page
      .getByRole("navigation", { name: "主导航" })
      .getByRole("button", { name: label, exact: true })
      .click();
    await expect(page.getByRole("main", { name: label })).toBeVisible();
    await expect(sidebar.getByRole("article", { name: "助手消息" })).toHaveCount(1);
  }
  await expect(sidebar.getByRole("textbox", { name: "对话输入" })).toHaveValue(
    "切页前的草稿",
  );

  // 场景 3：桌面收起后从顶栏重新打开，回复不丢。
  await sidebar.getByRole("button", { name: "收起对话栏" }).click();
  await expect(sidebar).toHaveCount(0);
  await page.getByRole("button", { name: "显示对话栏" }).click();
  await expect(sidebar.getByRole("article", { name: "助手消息" })).toHaveCount(1);

  // 场景 4：窄屏覆盖面板——打开后可关闭返回原页面。
  await page.setViewportSize({ width: 390, height: 844 });
  const overlay = page.getByRole("complementary", { name: "对话面板" });
  await expect(overlay).toBeVisible();
  await page.screenshot({ path: info.outputPath("02-chat-narrow.png") });
  await overlay.getByRole("button", { name: "关闭对话面板" }).click();
  await expect(overlay).toHaveCount(0);
  await expect(page.getByRole("main", { name: "项目概览" })).toBeVisible();

  // 场景 5：窄屏经顶栏按钮重新打开。
  await page.getByRole("button", { name: "显示对话栏" }).click();
  await expect(page.getByRole("complementary", { name: "对话面板" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("19.1 完整创作流程：讨论 → 任务建议（不自动开工）→ 显式开始 → 候选 → 接受 → 正式结果", async ({
  page,
}, info) => {
  await createProject(page, "怀表谜案");
  const sidebar = page.getByRole("complementary", { name: "对话栏" });

  // 场景 6：「帮我写第一章」只产生任务建议，不直接启动生成。
  await send(page, "帮我写第一章");
  const draft = sidebar.getByRole("article", { name: "任务建议" }).last();
  await expect(draft).toBeVisible({ timeout: 8000 });
  await expect(draft.getByText("生成章节草稿", { exact: true })).toBeVisible();
  // 范围不齐全：缺已接受节拍与基础版本，「开始」禁用并给出解决入口。
  const start = draft.getByRole("button", { name: /开始（创建创作任务）/ });
  await expect(start).toBeDisabled();
  await expect(draft.getByText(/还没有已接受的节拍/)).toBeVisible();
  await expect(draft.getByText(/缺少可靠的基础版本/)).toBeVisible();
  await expect(sidebar.getByRole("article", { name: "执行任务" })).toHaveCount(0);
  await page.screenshot({ path: info.outputPath("03-chat-draft-missing.png") });

  // 场景 7：输入框创作操作是显式入口——规划类范围齐全，可以开始。
  await sidebar.getByRole("button", { name: "✎ 创作操作" }).click();
  await sidebar.getByRole("button", { name: /规划故事与节拍/ }).click();
  const planDraft = sidebar.getByRole("article", { name: "任务建议" }).last();
  await expect(planDraft.getByRole("button", { name: /开始（创建创作任务）/ })).toBeEnabled();
  await page.screenshot({ path: info.outputPath("04-chat-draft-ready.png") });
  await planDraft.getByRole("button", { name: /开始（创建创作任务）/ }).click();

  // 场景 8：执行卡推进到候选待审阅。
  const execution = sidebar.getByRole("article", { name: "执行任务" });
  await expect(execution).toBeVisible();
  await expect(execution.getByText("等待审阅")).toBeVisible({ timeout: 8000 });
  const candidate = sidebar.getByRole("article", { name: "待审阅候选" });
  await expect(candidate).toBeVisible();
  await expect(candidate.getByText(/接受前正式内容不变/)).toBeVisible();
  await page.screenshot({ path: info.outputPath("05-chat-candidate.png") });

  // 场景 9：接受候选 → 结果卡以 Commit 回读为准。
  await candidate.getByRole("button", { name: "接受候选并提交" }).click();
  const result = sidebar.getByRole("article", { name: "已接受结果" });
  await expect(result).toBeVisible();
  await expect(result.getByText(/正式提交/)).toBeVisible();
  await expect(result.getByText(/sim-/)).toBeVisible();
  await expect(candidate.getByText("已接受")).toBeVisible();
  await page.screenshot({ path: info.outputPath("06-chat-result.png") });
});

test("19.1 停止与故障：部分回复保留，错误三要素，重试关联", async ({ page }, info) => {
  await createProject(page, "停止与故障");
  const sidebar = page.getByRole("complementary", { name: "对话栏" });

  // 场景 10：停止回复——已收到内容保留，标记为已停止；下一条草稿仍可编辑。
  await send(page, "长回复");
  const assistant = sidebar.getByRole("article", { name: "助手消息" });
  await expect(assistant.getByText(/长回复演示/)).toBeVisible({ timeout: 8000 });
  await sidebar.getByRole("button", { name: "停止回复" }).click();
  await expect(sidebar.getByText("已停止；以上是已收到的部分回复。")).toBeVisible();
  await sidebar.getByRole("textbox", { name: "对话输入" }).fill("换个方向讨论");
  await expect(sidebar.getByRole("button", { name: "发送", exact: true })).toBeEnabled();
  await page.screenshot({ path: info.outputPath("07-chat-stopped.png") });

  // 场景 11：故障注入——部分回复保留，错误带「为什么 + 怎样算对」。
  await send(page, "模拟失败");
  const failed = sidebar.getByRole("article", { name: "助手消息" }).last();
  await expect(failed.getByText(/模型连接中断/)).toBeVisible({ timeout: 8000 });
  await expect(failed.getByText(/怎样算对：/)).toBeVisible();
  await page.screenshot({ path: info.outputPath("08-chat-failed.png") });

  // 场景 12：重试这一轮——创建新尝试，不覆盖旧回复。
  await failed.getByRole("button", { name: "重试这一轮" }).click();
  await expect(sidebar.getByRole("article", { name: "助手消息" })).toHaveCount(3, {
    timeout: 8000,
  });
});

test("19.1 中文输入法组合输入不误发送", async ({ page }) => {
  await createProject(page, "输入法");
  const sidebar = page.getByRole("complementary", { name: "对话栏" });
  const input = sidebar.getByRole("textbox", { name: "对话输入" });
  await input.click();
  // 模拟中文输入法：组合输入期间按 Enter 不发送，组合结束后才发送。
  await input.evaluate((el: HTMLTextAreaElement) => {
    el.value = "雨夜";
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await input.dispatchEvent("compositionstart");
  await page.keyboard.press("Enter");
  await expect(sidebar.getByRole("article", { name: "作者消息" })).toHaveCount(0);
  await input.dispatchEvent("compositionend");
  await page.keyboard.press("Enter");
  await expect(sidebar.getByRole("article", { name: "作者消息" })).toHaveCount(1);
});

test("19.1 旧后端能力提示：没有对话服务时不展示虚假成功", async ({ page }) => {
  // 不带 ?chatMock=1：模拟器不装配，能力探测走真实端点，等同于「新前端连接旧后端」。
  await createProject(page, "旧后端核对", false);
  await page.getByRole("button", { name: "显示对话栏" }).click();
  const sidebar = page.getByRole("complementary", { name: "对话栏" });
  await expect(sidebar.getByText("当前后端不提供对话服务。")).toBeVisible();
  await expect(sidebar.getByText(/重启后端/)).toBeVisible();
  await expect(sidebar.getByRole("textbox", { name: "对话输入" })).toHaveCount(0);
  await expect(sidebar.getByText("模拟模式")).toHaveCount(0);
});

test("19.1 创作台布局：对话栏紧贴正文，次要面板收拢左列", async ({
  page,
  request,
}, info) => {
  const p = await (
    await request.post("/api/v2/projects", { data: { name: "布局走查" } })
  ).json();
  await request.post(`/api/v2/projects/${p.project_id}/chapters/import`, {
    data: {
      idempotency_key: "layout",
      title: "第一章 雨夜",
      text: "林岚递出怀表。\n\n他仍然信任协助者。",
    },
  });
  await page.goto(MOCK);
  await page.evaluate(
    ({ id, name }) => {
      localStorage.setItem(
        "hastings.v2.projects",
        JSON.stringify([{ projectId: id, name, source: "created" }]),
      );
      localStorage.setItem("hastings.v2.current", id);
    },
    { id: p.project_id as string, name: "布局走查" },
  );
  await page.reload();
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("button", { name: "创作台", exact: true })
    .click();
  const editor = page.getByRole("textbox", { name: "人工正文" });
  await expect(editor).toHaveValue(/林岚/);
  const layout = await page.evaluate(() => {
    const box = (el: Element) => {
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x), right: Math.round(r.right), w: Math.round(r.width) };
    };
    const section = document.querySelector(".studio-grid > section")!;
    const chat = document.querySelector("aside.chat-shell")!;
    const aside = document.querySelector(".studio-grid > aside")!;
    const secondary = document.querySelector(".panel-collapsible")!;
    return {
      section: box(section),
      chat: box(chat),
      aside: box(aside),
      secondary: box(secondary),
    };
  });
  // 次要面板收拢到左列（目录列内），不挡在正文与对话之间。
  expect(layout.secondary.right).toBeLessThanOrEqual(layout.section.x + 2);
  // 对话栏紧贴正文编辑区（间距不超过网格 gap）。
  expect(layout.chat.x).toBeGreaterThanOrEqual(layout.section.right - 2);
  expect(layout.chat.x - layout.section.right).toBeLessThanOrEqual(20);
  // 正文宽度显著大于目录列，未被挤压。
  expect(layout.section.w).toBeGreaterThan(layout.aside.w * 2);
  await page.screenshot({ path: info.outputPath("09-studio-chat-layout.png") });
});
