import { expect, test } from "@playwright/test";

test("新前端连接到旧后端时明确提示版本不匹配", async ({ page }) => {
  await page.route("**/api/model-selection", (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Not Found" }),
    }),
  );
  await page.route("**/api/provider-catalog", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ catalog: [{ name: "deepseek", models: [] }] }),
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "切换模型", exact: true }).click();
  await expect(
    page.getByRole("dialog").getByText(/前后端版本不匹配.*重启后端/),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "保存连接", exact: true }),
  ).toBeDisabled();
});

test("Ollama 检测、下载与设为当前模型保留在 V2 流程中", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "切换模型", exact: true }).click();
  await page.getByRole("button", { name: "本地模型", exact: true }).click();
  await expect(page.getByText("Ollama ve2e", { exact: false })).toBeVisible();
  await page
    .getByRole("button", { name: "一键下载", exact: true })
    .first()
    .click();
  await expect(page.getByText(/模型 qwen3:4b 下载完成/)).toBeVisible();
  await page.getByRole("button", { name: "设为当前模型", exact: true }).click();
  await expect(
    page.getByText("已切换到本地模型 qwen3:4b，后续任务生效。", {
      exact: true,
    }),
  ).toBeVisible();
  const current = await (await request.get("/api/model-selection")).json();
  expect(current.provider).toBe("ollama");
  expect(current.model).toBe("qwen3:4b");
});

test("按厂商连接、切换模型、刷新后保留，并接入本地服务", async ({
  page,
  request,
}, info) => {
  await page.goto("/");
  await page.getByRole("button", { name: "切换模型", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("heading", { name: "模型设置", exact: true }),
  ).toBeVisible();
  await expect(
    dialog.getByRole("textbox", { name: "自定义服务地址" }),
  ).toHaveCount(0);
  await dialog
    .getByLabel("厂商或服务", { exact: true })
    .selectOption("deepseek");
  await dialog
    .getByLabel("接入密钥", { exact: true })
    .fill("e2e-secret-key-only-for-tests");
  await dialog.getByRole("button", { name: "保存连接", exact: true }).click();
  await expect(dialog.getByLabel("已连接来源")).toHaveValue("deepseek");
  await expect(dialog.getByLabel("接入密钥", { exact: true })).toHaveValue("");
  await dialog.getByLabel("可选模型").selectOption("deepseek-v4-pro");
  await dialog.getByRole("button", { name: "使用此模型", exact: true }).click();
  await expect(
    dialog.getByText("已切换到 deepseek-v4-pro，后续任务生效。", {
      exact: true,
    }),
  ).toBeVisible();
  expect((await (await request.get("/api/model-selection")).json()).model).toBe(
    "deepseek-v4-pro",
  );
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain(
    "e2e-secret",
  );
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "切换模型", exact: true }),
  ).toContainText("deepseek-v4-pro");
  await page.reload();
  await expect(
    page.getByRole("button", { name: "切换模型", exact: true }),
  ).toContainText("deepseek-v4-pro");
  await page.getByRole("button", { name: "切换模型", exact: true }).click();
  await dialog
    .getByRole("button", { name: "订阅 / Coding Plan", exact: true })
    .click();
  await dialog
    .getByLabel("厂商或服务", { exact: true })
    .selectOption("zhipu-coding");
  await dialog.getByLabel("接入密钥", { exact: true }).fill("e2e-plan-key");
  await dialog.getByRole("button", { name: "保存连接", exact: true }).click();
  await expect(dialog.getByLabel("已连接来源")).toHaveValue("zhipu-coding");
  await dialog
    .getByRole("button", { name: "刷新模型列表", exact: true })
    .click();
  await expect(dialog.getByText(/此服务未提供模型列表/)).toBeVisible();
  await dialog.getByLabel("可选模型").selectOption("glm-5.3-flash");
  await dialog.getByRole("button", { name: "使用此模型", exact: true }).click();
  await expect(
    dialog.getByText("已切换到 glm-5.3-flash，后续任务生效。", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("model-plans.png"),
    fullPage: true,
  });
  await dialog.getByRole("button", { name: "本地模型", exact: true }).click();
  await dialog
    .getByLabel("厂商或服务", { exact: true })
    .selectOption("lmstudio");
  await expect(dialog.getByLabel("接入密钥", { exact: true })).toHaveCount(0);
  await dialog.getByRole("button", { name: "保存连接", exact: true }).click();
  await expect(dialog.getByLabel("可选模型")).toHaveValue("qwen3:4b");
  await dialog.getByRole("button", { name: "使用此模型", exact: true }).click();
  await expect(
    dialog.getByText("已切换到 qwen3:4b，后续任务生效。", { exact: true }),
  ).toBeVisible();
  const active = await (await request.get("/api/model-selection")).json();
  expect(active.provider).toBe("lmstudio");
  await page.screenshot({
    path: info.outputPath("model-local.png"),
    fullPage: true,
  });
});

test("鉴权失败保留原模型，并继续提供手动模型编号", async ({
  page,
  request,
}) => {
  await request.post("/api/providers/connect", {
    data: { catalog_id: "deepseek", api_key: "e2e-key" },
  });
  await request.post("/api/providers/connect", {
    data: { catalog_id: "lmstudio" },
  });
  await request.put("/api/model-selection", {
    data: { provider: "lmstudio", model: "qwen3:4b" },
  });
  await page.route("**/api/providers/*/model-choices", (route) =>
    route.fulfill({
      status: 422,
      contentType: "application/json",
      body: JSON.stringify({ detail: "API key 无效；请重新接入" }),
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "切换模型", exact: true }).click();
  await page.getByLabel("已连接来源").selectOption("deepseek");
  await page.getByRole("button", { name: "刷新模型列表", exact: true }).click();
  await expect(
    page.getByText("API key 无效；请重新接入", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(/当前：LM Studio 本地模型 · qwen3:4b/),
  ).toBeVisible();
  await page.getByText("手动指定模型编号", { exact: true }).click();
  await expect(page.getByLabel("模型编号", { exact: true })).toBeVisible();
});
