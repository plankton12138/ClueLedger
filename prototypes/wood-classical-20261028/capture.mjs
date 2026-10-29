import { chromium } from "../../frontend/node_modules/playwright/index.mjs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const directory = path.dirname(fileURLToPath(import.meta.url));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
  const failures = [];
  page.on("pageerror", (error) => failures.push(error.message));
  await page.goto(new URL("./index.html", import.meta.url).href);
  const names = ["01-writing-desk", "02-clue-evidence", "03-module-library"];
  for (let i = 0; i < names.length; i++) {
    await page.evaluate((number) => window.renderScreen(number), i);
    await page.locator("#app").screenshot({ path: path.join(directory, `${names[i]}.png`) });
  }
  if (failures.length) throw new Error(`Page errors: ${failures.join("; ")}`);
} finally {
  await browser.close();
}
