import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  base: "./",
  publicDir: false,
  build: {
    outDir: "dist-workbench",
    rollupOptions: { input: fileURLToPath(new URL("./workbench.html", import.meta.url)) },
  },
});
