import { test, expect, type Page, type TestInfo } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { ModelConfig, ThinkingLevel } from "../src/api/models";

const PORT = process.env.HASTINGS_E2E_PORT ?? "8931";
const LEVELS: ThinkingLevel[] = ["off", "low", "high", "max"];

async function prepare(page: Page) {
  // 浏览器用受控配置检查选择行为；不改本机连接、不向供应商发请求。
  let config: ModelConfig = {
    revision: "r1",
    selection: {
      connection_id: "c", model: "deepseek-flash", source: "saved", available: true,
      reason: "", thinking_level: null, thinking_levels: LEVELS,
    },
    connections: [{
      name: "c", display_name: "测试连接", catalog_id: "custom", api: "openai-responses",
      base_url: "https://example.invalid/v1", models: ["deepseek-flash", "gpt-6.1-sol", "unknown"],
      default_model: "deepseek-flash", has_key: true, masked_key: "", credential_storage: "system",
      auth_method: "api_key", auth_provider: "", account: "", auth_status: "ready",
    }],
  };
  let conflict = false;
  const writes: Record<string, unknown>[] = [];
  await page.route("**/api/model-connections", route => route.fulfill({ json: config }));
  await page.route("**/api/model-selection", async route => {
    const body = route.request().postDataJSON();
    writes.push(body);
    if (conflict) {
      conflict = false;
      config = { ...config, revision: "newer", selection: { ...config.selection, thinking_level: "low" } };
      await route.fulfill({ status: 409, json: { error: {
        code: "MODEL_CONFIG_CONFLICT", where: "model.selection", why: "配置文件已被修改",
        fix: "重新读取配置后再提交",
      } } });
      return;
    }
    expect(route.request().headers()["if-match"]).toBe(config.revision);
    config = {
      ...config, revision: `r${writes.length + 1}`,
      selection: {
        ...config.selection, model: body.model, thinking_level: body.thinking_level ?? null,
        thinking_levels: body.model === "deepseek-flash" ? LEVELS
          : body.model === "gpt-6.1-sol" ? ["low", "medium", "high", "xhigh", "max"] : [],
      },
    };
    await route.fulfill({ json: config });
  });
  const root = mkdtempSync(join(tmpdir(), "clueledger-thinking-"));
  const response = await page.request.post(`http://127.0.0.1:${PORT}/api/file-workspaces`, {
    data: { path: join(root, "思考深度验收"), create: true },
  });
  expect(response.ok()).toBeTruthy();
  const workspace = await response.json();
  await page.goto(`/`);
  await page.getByLabel("选择作品目录").selectOption(workspace.id);
  await expect(page.getByLabel("当前连接与模型")).toBeVisible();
  return { writes, failNext: () => { conflict = true; } };
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  const path = info.outputPath(name);
  await page.screenshot({ path });
  await info.attach(name, { path, contentType: "image/png" });
}

test("思考深度可保存、刷新恢复，并随模型切换恢复默认", async ({ page }, info) => {
  const { writes } = await prepare(page);
  const depth = page.getByLabel("思考深度");
  await expect(depth.locator("option")).toHaveText(["思考：默认", "思考：关闭", "思考：低", "思考：高", "思考：最大"]);
  await depth.selectOption("max");
  await expect(depth).toHaveValue("max");
  expect(writes[0]).toEqual({ connection_id: "c", model: "deepseek-flash", thinking_level: "max" });
  await page.reload();
  await expect(depth).toHaveValue("max");
  await screenshot(page, info, "thinking-depth-1440.png");

  await page.getByLabel("当前连接与模型").selectOption("c::gpt-6.1-sol");
  await expect(depth).toHaveValue("");
  await expect(depth.locator("option")).toHaveText(["思考：默认", "思考：低", "思考：中", "思考：高", "思考：极高", "思考：最大"]);
  await depth.selectOption("high");
  await expect(depth).toHaveValue("high");
  await depth.selectOption("");
  await expect(depth).toHaveValue("");
  expect(writes.at(-1)?.thinking_level).toBeNull();
  await page.getByLabel("当前连接与模型").selectOption("c::unknown");
  await expect(depth).toBeDisabled();
});

test("思考深度冲突保留原选择，并能读取最新设置", async ({ page }) => {
  const { failNext } = await prepare(page);
  const depth = page.getByLabel("思考深度");
  await depth.selectOption("high");
  await expect(depth).toHaveValue("high");
  failNext();
  await depth.selectOption("max");
  await expect(page.getByRole("alert")).toContainText("[MODEL_CONFIG_CONFLICT]");
  await expect(depth).toHaveValue("high");
  await page.getByRole("button", { name: "重新读取", exact: true }).click();
  await expect(depth).toHaveValue("low");
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("窄对话栏与放大字号仍能操作思考深度", async ({ page }, info) => {
  await prepare(page);
  const handle = page.getByRole("separator", { name: "对话栏宽度" });
  for (let index = 0; index < 7; index++) await handle.press("ArrowRight");
  const depth = page.getByLabel("思考深度");
  await expect(depth).toBeVisible();
  await depth.selectOption("low");
  await expect(depth).toHaveValue("low");
  await screenshot(page, info, "thinking-depth-narrow.png");

  await page.setViewportSize({ width: 640, height: 960 });
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByLabel("调整字体大小").selectOption("150");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "对话", exact: true }).click();
  await expect(depth).toBeVisible();
  await depth.selectOption("high");
  await expect(depth).toHaveValue("high");
  await screenshot(page, info, "thinking-depth-640.png");
});
