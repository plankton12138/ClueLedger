import { test, expect } from "./fixtures/frontendExperience";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
async function shot(page: Page, name: string) {
  const path = resolve("../dev-notes/v0.8/artifacts/frontend-experience/screenshots"); mkdirSync(path, { recursive: true });
  await page.screenshot({ path: `${path}/H3-${name}.png` });
}
test("七种预设字段按真实契约往返，数字0与布尔false保留", async ({ page, request }) => {
  test.setTimeout(60_000);
  const presets = await request.get("/api/module-library/presets").then(r => r.json());
  expect(presets).toHaveLength(7);
  await page.goto("/");
  for (const preset of presets) {
    const field = preset.allowed_fields[0];
    const created = await request.post("/api/module-library/modules", { data: { kind: preset.kind, name: `字段往返-${preset.kind}`, request_key: crypto.randomUUID(), entries: [{ entry_id: "first", title: "字段条目", body: "隔离正文。", fields: { [field]: 0 } }] } }).then(r => r.json());
    expect(created.module).toBeTruthy();
    await page.evaluate(hash => { location.hash = hash; }, `#/library/modules/${created.module.module_id}/revisions/${created.module.head}`);
    const library = page.getByRole("region", { name: "模块库", exact: true });
    await expect(library.locator(".module-preview")).toContainText("隔离正文。");
    await expect(library.getByLabel("条目正文")).toHaveCount(0);
    await library.getByRole("button", { name: "编辑", exact: true }).click();
    const save = library.getByRole("button", { name: "保存新版本", exact: true });
    await expect(save).toBeDisabled();
    const control = library.locator(".entry-field-control").first();
    await expect(control.locator("input").first()).toHaveValue("0");
    await control.locator("input").first().fill("2");
    await save.click();
    await expect(library.getByText("已保存到模块库；其他作品保持原版本。", { exact: true })).toBeVisible();
    let revision = await library.getByLabel("模块版本").inputValue();
    let detail = await request.get(`/api/module-library/modules/${created.module.module_id}/revisions/${revision}`).then(r => r.json());
    expect(detail.entries[0].fields[field]).toBe(2);
    await control.locator("select").selectOption("boolean");
    const previous = revision;
    await save.click();
    await expect(library.getByLabel("模块版本")).not.toHaveValue(previous);
    revision = await library.getByLabel("模块版本").inputValue();
    detail = await request.get(`/api/module-library/modules/${created.module.module_id}/revisions/${revision}`).then(r => r.json());
    expect(detail.entries[0].fields[field]).toBe(false);
    await expect(library.getByRole("button", { name: "重试应用已发布版本", exact: true })).toHaveCount(0);
  }
  await shot(page, "entry-fields-1440");
});

test("高级字段异常保留输入并阻止发布，标量列表可在修正后发布", async ({ page, book, request }) => {
  const listing = await request.get("/api/module-library/modules?limit=50").then(r => r.json());
  const info = listing.items.find((item: { module_id: string }) => item.module_id === book.moduleId);
  await page.goto(`/#/library/modules/${book.moduleId}/revisions/${info.head}?workspace=${book.id}`);
  const library = page.getByRole("region", { name: "模块库", exact: true });
  await library.getByRole("button", { name: "编辑", exact: true }).click();
  await library.locator(".advanced-fields summary").last().click();
  const fields = library.getByLabel("条目字段", { exact: true }).last();
  const save = library.getByRole("button", { name: "保存新版本", exact: true });
  for (const text of ['{"unknown":"保留"}', '{"source":{"nested":true}}', '{损坏']) {
    await fields.fill(text); await expect(save).toBeDisabled();
    await expect(library.locator(".ui-field-error").last()).toBeVisible();
    await page.getByRole("link", { name: "写作", exact: true }).click();
    await expect(page.getByRole("region", { name: "正文阅读", exact: true })).toBeVisible();
    await page.goBack();
    const resume = library.getByRole("button", { name: "继续编辑", exact: true });
    await expect(resume.or(fields)).toBeVisible();
    if (await resume.isVisible()) await resume.click();
    await expect(fields).toHaveValue(text);
  }
  await shot(page, "invalid-fields-preserved-1440");
  await fields.fill('{"source":["档案",1,true]}'); await save.click();
  await expect(library.getByText("已保存到模块库；其他作品保持原版本。", { exact: true })).toBeVisible();
  const revision = await library.getByLabel("模块版本").inputValue();
  const detail = await request.get(`/api/module-library/modules/${book.moduleId}/revisions/${revision}`).then(r => r.json());
  expect(detail.entries.find((entry: { entry_id: string }) => entry.entry_id === "rain").fields.source).toEqual(["档案", 1, true]);
  const revisions = await request.get(`/api/module-library/modules/${book.moduleId}/revisions`).then(r => r.json());
  expect(revisions.total).toBe(3);
});

test("窄窗口模块预览与编辑可返回列表，失败变体保留名称", async ({ page, book }) => {
  await page.setViewportSize({ width: 640, height: 800 });
  await page.goto(`/#/library?workspace=${book.id}`);
  const library = page.getByRole("region", { name: "模块库", exact: true });
  await library.getByLabel("搜索模块").fill(book.id); await library.getByLabel("搜索模块").press("Enter");
  await library.getByRole("button", { name: new RegExp(`长记录-${book.id}`) }).click();
  await expect(library.locator(".module-preview")).toContainText(book.body);
  await expect(library.getByRole("complementary", { name: "浏览模块库", exact: true, includeHidden: true })).toBeHidden();
  await shot(page, "preview-640");
  await library.getByRole("button", { name: "编辑", exact: true }).click();
  await library.getByLabel("条目正文").last().fill("窄窗口草稿");
  await shot(page, "editor-640");
  await library.getByRole("button", { name: "返回列表", exact: true }).click();
  await expect(library.getByLabel("搜索模块")).toHaveValue(book.id);
  await library.getByRole("button", { name: new RegExp(`长记录-${book.id}`) }).click();
  await library.getByRole("button", { name: "继续编辑", exact: true }).click();
  await expect(library.getByLabel("条目正文").last()).toHaveValue("窄窗口草稿");
  await page.route(`**/api/module-library/modules/${book.moduleId}/fork`, route => route.fulfill({ status: 409, json: { error: { code: "MODULE_HEAD_STALE_CONFLICT", where: "library", why: "版本已变化", fix: "核对版本后重试" } } }));
  await library.getByRole("button", { name: "另存变体", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "另存变体", exact: true });
  await dialog.getByLabel("变体名称").fill("仍要保留的名称"); await dialog.getByRole("button", { name: "另存", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("MODULE_HEAD_STALE_CONFLICT");
  await expect(dialog.getByLabel("变体名称")).toHaveValue("仍要保留的名称");
});
