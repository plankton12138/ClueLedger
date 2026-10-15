import { expect, test } from "@playwright/test";

test("J7 变更中心：分支修订后可拒绝补丁并保留未解决影响", async ({ page, request }, testInfo) => {
  const created = await request.post("/api/v2/projects", { data: { name: "J7 悬疑项目" } });
  expect(created.ok()).toBeTruthy();
  const project = (await created.json()).project_id as string;
  const imported = await request.post(`/api/v2/projects/${project}/chapters/import`, {
    data: { idempotency_key: "import", title: "第一章", text: "他仍然相信协助者。" },
  });
  expect(imported.ok()).toBeTruthy();
  const manuscript = await (await request.get(`/api/v2/projects/${project}/manuscript`)).json();
  const unit = manuscript.chapters[0].units[0].unit_id as string;
  const content = { schema_version: 1, unit_id: unit, chapter_id: manuscript.chapters[0].chapter_id,
    text: "他开始怀疑协助者。", state: "active", origin: "import", scene_id: null, derived_from: [] };
  const opened = await request.post(`/api/v2/projects/${project}/changes`, { data: {
    idempotency_key: "open", intent: "协助者动机变化", base_commit_id: manuscript.commit_id,
    seed_ids: [unit], seed_patches: { [unit]: content },
  } });
  expect(opened.ok()).toBeTruthy();
  const cs = (await opened.json()).change_set_id as string;
  await page.goto("/");
  await page.getByRole("button", { name: "变更中心", exact: true }).click();
  await page.getByRole("textbox", { name: "项目", exact: true }).fill(project);
  await page.getByRole("button", { name: "载入变更" }).click();
  await page.getByRole("button", { name: /协助者动机变化/ }).click();
  await page.getByRole("button", { name: "分析影响", exact: true }).click();
  await page.getByRole("button", { name: "制定修订计划" }).click();
  await expect(page.getByText("批准前确认", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "批准所示范围与计划" }).click();
  await page.getByText("提供候选修订内容", { exact: true }).click();
  await page.getByRole("textbox", { name: "候选修订内容" }).fill(JSON.stringify({ [unit]: content }));
  await page.getByRole("button", { name: "在分支执行修订" }).click();
  await page.getByRole("button", { name: "生成审阅差异" }).click();
  await expect(page.getByRole("checkbox")).toHaveCount(1);
  await page.screenshot({ path: testInfo.outputPath("change-center.png"), fullPage: true });
  // 不勾选即拒绝；正式稿保持原文，影响项仍 open。
  await page.getByRole("button", { name: "提交所选修改" }).click();
  await expect(page.getByText("已提交", { exact: true }).first()).toBeVisible();
  const detail = await (await request.get(`/api/v2/projects/${project}/changes/${cs}`)).json();
  const report = detail.objects.find((o: { kind: string }) => o.kind === "changes.impact_report");
  expect(report.content.items[0].status).toBe("open");
  const saved = await (await request.get(`/api/v2/projects/${project}/manuscript`)).json();
  expect(saved.chapters[0].units[0].text).toBe("他仍然相信协助者。");
});
