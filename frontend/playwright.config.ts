import { defineConfig } from "@playwright/test";

const port = Number(process.env.HASTINGS_E2E_PORT ?? "8931");

/**
 * E2E 配置（dev-notes/v0.1/e2e-test-plan.md §4.2）：单进程真服务——webServer 先 build
 * 前端再起 uv 驱动器（tests/e2e_server.py，API + 静态托管同进程，端口 8931）。
 * 零重试政策（retries: 0）：红了先查确定性，flaky 即缺陷；旅程用唯一 slug 隔离，
 * workers 固定 1 保证跨文件顺序确定。
 */
export default defineConfig({
  testDir: "./e2e",
  // j1–j8 是旧 V1 壳的历史旅程；V2 主界面从此套真实服务旅程验收。
  testMatch: "v2-*.spec.ts",
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
    command: `npm run build && uv run --directory .. python -m tests.e2e_server --port ${port}`,
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: !process.env.CI && !process.env.HASTINGS_E2E_PORT,
    timeout: 120_000,
  },
});
