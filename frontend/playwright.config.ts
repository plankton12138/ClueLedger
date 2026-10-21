import { defineConfig } from "@playwright/test";

const port = Number(process.env.HASTINGS_E2E_PORT ?? "8931");

/**
 * E2E 配置：单进程真服务——webServer 先 build 前端再启动 `hastings.server`
 * （文件接口 + 静态托管同进程，端口 8931）。
 *
 * 状态目录固定指向仓库内 `data/test-tmp/e2e-state`（已被 .gitignore 覆盖），
 * 夹具自己创建唯一作品路径，避免跨运行串味。
 * 零重试政策（retries: 0）：红了先查确定性，flaky 即缺陷。
 */
export default defineConfig({
  testDir: "./e2e",
  testMatch: "workspace.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
  },
  webServer: {
    command: `npm run build && uv run --directory .. python -m hastings.server --port ${port} --state-dir ../data/test-tmp/e2e-state`,
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: !process.env.CI && !process.env.HASTINGS_E2E_PORT,
    timeout: 120_000,
  },
});
