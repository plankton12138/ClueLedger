import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 开发形态（计划 D1）：Vite dev server 托管前端，/api 与 /ws 代理到
// uv run python -m hastings.server 启动的 FastAPI 服务（默认 8000）。
// 账单走独立支付服务；可用环境变量在测试时改到其他端口。
const apiTarget = process.env.CLUE_API_TARGET || "http://localhost:8000";
const billingTarget = process.env.CLUE_BILLING_TARGET || "http://localhost:8300";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    proxy: {
      // 账单走独立支付服务；生产由反向代理映射同一路径，浏览器仍视为同源。
      "/api/billing": billingTarget,
      "/api": apiTarget,
      "/ws": { target: apiTarget.replace(/^http/, "ws"), ws: true },
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
