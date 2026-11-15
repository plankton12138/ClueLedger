import { fileURLToPath, URL } from "node:url";
import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 开发形态（计划 D1）：Vite dev server 托管前端，/api 与 /ws 代理到
// uv run python -m hastings.server 启动的 FastAPI 服务（默认 8000）。
// 账单走独立支付服务；可用环境变量在测试时改到其他端口。
// 另有独立入口 eval-workbench.html：评测工作台，不进产品路由；构建后由 evaluation workbench 托管。
const apiTarget = process.env.CLUE_API_TARGET || "http://localhost:8000";
const billingTarget = process.env.CLUE_BILLING_TARGET || "http://localhost:8300";
const workbenchTarget = process.env.CLUE_EVAL_WORKBENCH_TARGET || "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        "eval-workbench": resolve(__dirname, "eval-workbench.html"),
      },
    },
  },
  server: {
    proxy: {
      // 账单走独立支付服务；生产由反向代理映射同一路径，浏览器仍视为同源。
      "/api/billing": billingTarget,
      // 评测工作台本地 API（仅开发 eval-workbench 入口时用）。
      "/api/runs": workbenchTarget,
      "/api/attribution-options": workbenchTarget,
      "/api": apiTarget,
      "/ws": { target: apiTarget.replace(/^http/, "ws"), ws: true },
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
