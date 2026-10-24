// Read-only probe: what does the materials page actually receive and render?
import { createRequire } from 'node:module';
import { writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
const require = createRequire(join(process.cwd(), 'frontend/package.json'));
const { chromium } = require('playwright');
const [section = 'characters'] = process.argv.slice(2);
const out = fileURLToPath(new URL('.', import.meta.url));
const base = 'http://127.0.0.1:8947';
const workspaceId = 'b9b12fe248ca6299';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
const seen = [];
page.on('response', async (r) => {
  if (!r.url().includes('story-materials')) return;
  let body = null;
  try { body = await r.json(); } catch { body = null; }
  seen.push({
    url: r.url().replace(base, ''),
    status: r.status(),
    entries: Array.isArray(body?.entries) ? body.entries.length : null,
    total: body?.total ?? null,
    as_of: body?.as_of_chapter ?? null,
    current: body?.current_chapter ?? null,
  });
});
await page.goto(`${base}/#/workspaces/${workspaceId}/materials/${section}`);
await page.waitForTimeout(6000);
const body = await page.locator('body').innerText();
const listItems = await page.locator('ul > li').count();
await page.screenshot({ path: join(out, `probe-${section}.png`), fullPage: true });
mkdirSync(out, { recursive: true });
writeFileSync(join(out, `probe-${section}.json`), JSON.stringify({ seen, listItems, body }, null, 2), 'utf8');
console.log(JSON.stringify({ seen, listItems, hasEmptyText: body.includes('还没有角色小传') }, null, 2));
await browser.close();
