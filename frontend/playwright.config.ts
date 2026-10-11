import { defineConfig } from "@playwright/test";

/**
 * E2E 配置（dev-notes/e2e-test-plan.md §4.2）：单进程真服务——webServer 先 build
 * 前端再起 uv 驱动器（tests/e2e_server.py，API + 静态托管同进程，端口 8931）。
 * 零重试政策（retries: 0）：红了先查确定性，flaky 即缺陷；旅程用唯一 slug 隔离，
 * workers 固定 1 保证跨文件顺序确定。
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:8931",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run build && uv run --directory .. python -m tests.e2e_server --port 8931",
    url: "http://127.0.0.1:8931/api/health",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
