import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

const port = Number(process.env.HASTINGS_E2E_PORT ?? "8931");
const dataDir = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e");

/**
 * E2E 配置：单进程真服务——webServer 先 build 前端再启动 `hastings.server`
 * （文件接口 + 静态托管同进程，端口 8931）。
 *
 * 状态目录固定指向仓库内 `data/test-tmp/e2e-state`（已被 .gitignore 覆盖），
 * 夹具自己创建唯一作品路径，避免跨运行串味。
 *
 * 两个项目：
 * - `workspace`：不调用模型的确定性回归（目录、阅读、Git、布局、渲染计数）。
 * - `online`：真的调用在线模型，检查「说话 → 登记范围 → 写入 → 检查」在浏览器里接通；
 *   断言只放可判定的事实，耗时按模型响应放宽。
 *
 * `npm run test:e2e` 两个都跑；只想快速回归本地部分时用 `npm run test:e2e:local`。
 * 零重试政策（retries: 0）：红了先查确定性；在线模型的失败按端点/网络、产品接线、
 * 模型理解三类留证判断，不用重试掩盖。
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "workspace",
      testMatch: ["workspace.spec.ts", "chat-rendering.spec.ts", "frontend-experience.spec.ts", "frontend-foundation.spec.ts", "module-editor.spec.ts", "module-workspace.spec.ts", "ideas-companion.spec.ts", "core-pages.spec.ts", "shared-interactions.spec.ts", "story-overview.spec.ts"],
      timeout: 30_000,
    },
    {
      name: "online",
      testMatch: ["model-flows.spec.ts", "activity.spec.ts"],
      timeout: 240_000,
    },
  ],
  webServer: {
    command: `npm run build && uv run --directory .. python -m hastings.server --port ${port} --state-dir "${dataDir}/state" --library-dir "${dataDir}/library"`,
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: !process.env.CI && !process.env.HASTINGS_E2E_PORT,
    timeout: 120_000,
  },
});
