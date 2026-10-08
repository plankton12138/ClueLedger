import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 开发形态（计划 D1）：Vite dev server 托管前端，/api 与 /ws 代理到
// uv run python -m hastings.server 启动的 FastAPI 服务（默认 8000）。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
