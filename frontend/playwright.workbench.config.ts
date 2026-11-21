import { defineConfig } from "@playwright/test";

const origin = "http://127.0.0.1:8942";
const outDir = "node_modules/.cache/workbench-e2e";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "evaluation-workbench.spec.ts",
  use: { baseURL: origin },
  webServer: {
    command: `npm run build:workbench -- --outDir ${outDir} && vite preview --config vite.workbench.config.ts --outDir ${outDir} --host 127.0.0.1 --port 8942 --strictPort`,
    url: `${origin}/workbench.html`,
  },
});
