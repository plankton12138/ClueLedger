import { chromium } from "../../frontend/node_modules/playwright/index.mjs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const directory = path.dirname(fileURLToPath(import.meta.url));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1080 }, deviceScaleFactor: 1 });
  const failures = [];
  page.on("pageerror", (error) => failures.push(error.message));
  await page.goto(new URL("./index.html", import.meta.url).href);
  const count = await page.locator("#switcher button").count();
  if (count !== 7) throw new Error(`Expected seven desktop screens, got ${count}`);
  for (let i = 0; i < count; i++) {
    await page.evaluate((number) => window.renderScreen(number), i);
    const number = String(i + 1).padStart(2, "0");
    await page.locator("#app").screenshot({ path: path.join(directory, `${number}.png`) });
  }
  if (failures.length) throw new Error(`Page errors: ${failures.join("; ")}`);
  await page.goto(new URL("./overview.html", import.meta.url).href);
  await page.locator(".card").last().waitFor();
  await page.locator(".board").screenshot({ path: path.join(directory, "overview.png") });
} finally {
  await browser.close();
}
