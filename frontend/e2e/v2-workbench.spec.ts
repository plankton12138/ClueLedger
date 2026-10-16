import {
  expect,
  test,
  type Page,
  type APIRequestContext,
} from "@playwright/test";

async function create(page: Page, name: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("textbox", { name: "新故事名称" }).fill(name);
  await page.getByRole("button", { name: "新建故事", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  return (await page.evaluate(() =>
    localStorage.getItem("hastings.v2.current"),
  ))!;
}
async function registered(
  page: Page,
  request: APIRequestContext,
  name: string,
) {
  const p = await (
    await request.post("/api/v2/projects", { data: { name } })
  ).json();
  const imported = await (
    await request.post(`/api/v2/projects/${p.project_id}/chapters/import`, {
      data: {
        idempotency_key: "chapter",
        title: "第一章 雨夜",
        text: "林岚递出怀表。\n\n他仍然信任协助者。",
      },
    })
  ).json();
  await page.goto("/");
  await page.evaluate(
    ({ id, name }) => {
      localStorage.setItem(
        "hastings.v2.projects",
        JSON.stringify([{ projectId: id, name, source: "created" }]),
      );
      localStorage.setItem("hastings.v2.current", id);
    },
    { id: p.project_id as string, name },
  );
  await page.reload();
  return { pid: p.project_id as string, imported };
}
test("七页导航、空状态、新建项目与响应式外壳", async ({ page }, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await create(page, "雾港来信");
  await expect(page.locator(".topbar")).toHaveCSS(
    "background-image",
    /linear-gradient/,
  );
  await expect(page.locator(".panel").first()).toHaveCSS(
    "border-top-width",
    "1px",
  );
  for (const label of [
    "创作台",
    "故事账本",
    "变更影响",
    "修订审阅",
    "悬疑审计",
    "作者模型",
    "项目概览",
  ]) {
    await page
      .getByRole("navigation", { name: "主导航" })
      .getByRole("button", { name: label, exact: true })
      .click();
    await expect(page.getByRole("main", { name: label })).toBeVisible();
  }
  await page.screenshot({
    path: info.outputPath("01-overview.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.getByRole("button", { name: "创作台", exact: true }).click();
  await expect(page.getByRole("main", { name: "创作台" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("规划 → 接受 → 生成正文 → 人工保存 → 作者偏好声明与撤销", async ({
  page,
  request,
}, info) => {
  const pid = await create(page, "怀表谜案");
  await page.getByRole("button", { name: "创作台", exact: true }).click();
  await page
    .getByRole("textbox", { name: "创作要求" })
    .fill("规划林岚递出怀表的一章与节拍");
  await page.getByRole("button", { name: "提交协作任务" }).click();
  const planner = page.getByRole("article", { name: "策划任务" });
  await expect(planner.getByText("等待审阅", { exact: true })).toBeVisible();
  expect(
    (await (await request.get(`/api/v2/projects/${pid}/story`)).json())
      .artifacts,
  ).toHaveLength(0);
  await planner.getByRole("button", { name: "接受候选并提交" }).click();
  await expect(planner.getByText(/正式提交/)).toBeVisible();
  await page.getByRole("combobox", { name: "协作任务" }).selectOption("writer");
  await page
    .getByRole("combobox", { name: "已接受节拍" })
    .selectOption(`${pid}:beat`);
  await page
    .getByRole("textbox", { name: "创作要求" })
    .fill("写出怀表交接的第一章");
  await page.getByRole("button", { name: "提交协作任务" }).click();
  const writer = page.getByRole("article", { name: "写作任务" });
  await expect(writer.getByText("等待审阅", { exact: true })).toBeVisible();
  await writer.getByRole("button", { name: "接受候选并提交" }).click();
  await expect(page.getByRole("textbox", { name: "人工正文" })).toBeEnabled();
  const edit = page.getByRole("textbox", { name: "人工正文" });
  await edit.fill("林岚递出怀表。\n\n他仍然信任这位协助者。");
  await page.getByRole("button", { name: "保存正文", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "保存正文", exact: true }),
  ).toBeDisabled();
  await page.screenshot({
    path: info.outputPath("02-studio.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "故事账本", exact: true }).click();
  await expect(page.getByText("林岚", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "三层事实" }).click();
  await page.getByRole("button", { name: "角色认知", exact: true }).click();
  await page.screenshot({
    path: info.outputPath("03-ledger.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "作者模型", exact: true }).click();
  await page
    .getByRole("textbox", { name: "写作偏好声明" })
    .fill("少用解释性独白，用动作表达情绪。");
  await page.getByRole("button", { name: "声明并生效" }).click();
  await page.getByRole("button", { name: /少用解释性独白/ }).click();
  await page.getByRole("textbox", { name: "撤销偏好原因" }).fill("本章不适用");
  await page.getByRole("button", { name: "撤销这条偏好" }).click();
  await expect(page.getByText("已撤销", { exact: true }).last()).toBeVisible();
  await page.screenshot({
    path: info.outputPath("07-author.png"),
    fullPage: true,
  });
});

test("正文冲突保留草稿，切换项目需明确放弃；版本恢复不删除历史", async ({
  page,
  request,
}) => {
  const { pid, imported } = await registered(page, request, "冲突保护");
  const another = await (
    await request.post("/api/v2/projects", { data: { name: "另一部作品" } })
  ).json();
  await page.evaluate((p) => {
    const registry = JSON.parse(localStorage.getItem("hastings.v2.projects")!);
    localStorage.setItem(
      "hastings.v2.projects",
      JSON.stringify([
        ...registry,
        { projectId: p.project_id, name: p.name, source: "created" },
      ]),
    );
  }, another);
  await page.reload();
  await page.getByRole("button", { name: "创作台", exact: true }).click();
  const editor = page.getByRole("textbox", { name: "人工正文" });
  await expect(editor).toHaveValue(/林岚/);
  await editor.fill("切换前的未保存草稿");
  await page.getByRole("combobox", { name: "选择项目" }).click();
  await page.getByRole("option", { name: "另一部作品" }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "返回编辑" }).click();
  await expect(editor).toHaveValue("切换前的未保存草稿");
  await page.getByRole("combobox", { name: "选择项目" }).click();
  await page.getByRole("option", { name: "另一部作品" }).click();
  await page.getByRole("button", { name: "放弃修改并切换" }).click();
  await expect(page.getByRole("textbox", { name: "人工正文" })).toHaveCount(0);
  await page.getByRole("combobox", { name: "选择项目" }).click();
  await page.getByRole("option", { name: "冲突保护" }).click();
  await expect(editor).toHaveValue(/林岚/);
  const ms = await (
    await request.get(`/api/v2/projects/${pid}/manuscript`)
  ).json();
  const chapter = ms.chapters[0];
  await editor.fill("我的未保存草稿");
  const saved = await request.post(
    `/api/v2/projects/${pid}/chapters/${chapter.chapter_id}/save`,
    {
      data: {
        idempotency_key: "external",
        full_text: "另一窗口的稿子",
        expected_chapter_base: chapter.chapter_base,
      },
    },
  );
  expect(saved.ok()).toBeTruthy();
  await page.getByRole("button", { name: "保存正文", exact: true }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: /过期|冲突|变化/ }),
  ).toBeVisible();
  await expect(editor).toHaveValue("我的未保存草稿");
  await page.getByRole("button", { name: "重新读取当前章" }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "返回编辑" }).click();
  await expect(editor).toHaveValue("我的未保存草稿");
  await page.getByRole("button", { name: "重新读取当前章" }).click();
  await page.getByRole("button", { name: "放弃修改并切换" }).click();
  await expect(editor).toHaveValue("另一窗口的稿子");
  await page
    .getByRole("textbox", { name: "恢复来源提交" })
    .fill(imported.commit_id);
  await page.getByRole("button", { name: "恢复为当前正文" }).click();
  await expect(editor).toHaveValue(/林岚/);
});

for (const accept of [false, true])
  test(`变更影响 → 批准 → 分支修订 → ${accept ? "接受" : "拒绝"}补丁 → 审计与导出`, async ({
    page,
    request,
  }, info) => {
    const { pid } = await registered(page, request, "局部修订");
    const ms = await (
      await request.get(`/api/v2/projects/${pid}/manuscript`)
    ).json();
    const unit = ms.chapters[0].units[0].unit_id as string;
    const opened = await request.post(`/api/v2/projects/${pid}/changes`, {
      data: {
        idempotency_key: "change",
        intent: "协助者动机变化",
        base_commit_id: ms.commit_id,
        seed_ids: [unit],
        seed_patches: {
          [unit]: {
            schema_version: 1,
            unit_id: unit,
            chapter_id: ms.chapters[0].chapter_id,
            text: "他开始怀疑协助者。",
            state: "active",
            origin: "import",
            scene_id: null,
            derived_from: [],
          },
        },
      },
    });
    expect(opened.ok()).toBeTruthy();
    const cs = (await opened.json()).change_set_id;
    await page.reload();
    await page.getByRole("button", { name: "变更影响", exact: true }).click();
    await page.getByRole("button", { name: "分析影响", exact: true }).click();
    await page.getByRole("button", { name: "制定修订计划" }).click();
    await page.screenshot({
      path: info.outputPath("04-impact.png"),
      fullPage: true,
    });
    await page.getByRole("button", { name: "批准所示范围与计划" }).click();
    await page.getByRole("button", { name: "在分支执行修订" }).click();
    await page.getByRole("button", { name: "生成审阅差异" }).click();
    await page.getByRole("button", { name: "进入修订审阅" }).click();
    await expect(page.getByRole("checkbox")).toHaveCount(1);
    if (accept) await page.getByRole("checkbox").check();
    await page.screenshot({
      path: info.outputPath("05-review.png"),
      fullPage: true,
    });
    await page.getByRole("button", { name: "提交所选差异" }).click();
    await expect(
      page.getByRole("status").filter({ hasText: /所选差异已提交/ }),
    ).toBeVisible();
    const detail = await (
      await request.get(`/api/v2/projects/${pid}/changes/${cs}`)
    ).json();
    expect(detail.change_set.status).toBe("committed");
    const saved = await (
      await request.get(`/api/v2/projects/${pid}/manuscript`)
    ).json();
    expect(saved.chapters[0].units[0].text).toBe(
      accept ? "他开始怀疑协助者。" : "林岚递出怀表。",
    );
    await page.getByRole("button", { name: "悬疑审计", exact: true }).click();
    const auditing = page.waitForResponse(
      (r) => r.url().endsWith("/audit") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "运行一致性审计" }).click();
    expect((await auditing).ok()).toBeTruthy();
    await expect(
      page.getByRole("button", { name: "运行一致性审计" }),
    ).toBeEnabled();
    await page.screenshot({
      path: info.outputPath("06-audit.png"),
      fullPage: true,
    });
    const zip = await request.get(`/api/v2/projects/${pid}/export`);
    expect(zip.ok()).toBeTruthy();
    expect(zip.headers()["content-type"]).toContain("zip");
  });

test("设置只管理原生项目，不读取旧工作区", async ({ page }) => {
  const legacyRequests: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/v2-migration/") || r.url().endsWith("/api/workspaces"))
      legacyRequests.push(r.url());
  });
  await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "新故事名称" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "已有项目编号" })).toBeVisible();
  await expect(page.getByText("旧工作区迁移", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "待迁移工作区" })).toHaveCount(0);
  expect(legacyRequests).toEqual([]);
});
