// Real online case, no retry. Run from repository root with <local-root> <action> [args].
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const require = createRequire(join(process.cwd(), 'frontend/package.json'));
const { chromium } = require('playwright');
const [localRoot, action, ...args] = process.argv.slice(2);
const out = fileURLToPath(new URL('.', import.meta.url));
const base = 'http://127.0.0.1:8947';
const save = (name, value, local = false) => {
  const path = join(local ? localRoot : out, name);
  mkdirSync(resolve(path, '..'), { recursive: true });
  writeFileSync(path, typeof value === 'string' ? value : JSON.stringify(value, null, 2), 'utf8');
};
const read = (name) => JSON.parse(readFileSync(join(out, name), 'utf8'));
async function api(path, body) {
  const response = await fetch(base + '/api' + path, {
    headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { method: 'POST', body: JSON.stringify(body) }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(JSON.stringify({ path, status: response.status, data }));
  return data;
}
async function browserPage() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  page.setDefaultTimeout(20000);
  await page.goto(base);
  await page.getByLabel('选择作品目录').selectOption(read('run.json').workspace.id);
  return { browser, page };
}
async function start() {
  const config = await api('/model-connections');
  const connection = config.connections.find(x => x.name === config.selection.connection_id);
  if (!connection || ['localhost', '127.0.0.1', '[::1]'].includes(new URL(connection.base_url).hostname))
    throw new Error('An online selected connection is required');
  const workspace = await api('/file-workspaces', { path: join(localRoot, '雪山温泉案例'), create: true });
  save('run.json', { workspace, selection: config.selection, endpoint_host: new URL(connection.base_url).hostname,
    started_at: new Date().toISOString(), local_root: localRoot, commit: 'c32cbaa', retries: 0 });
  console.log(JSON.stringify({ workspace: workspace.id, model: config.selection, host: new URL(connection.base_url).hostname }));
  await send('01-original', join(out, 'prompt-original.txt'), true);
}
async function send(stage, promptPath, fresh = false) {
  const { browser, page } = await browserPage();
  try {
    await page.getByRole('button', { name: '写作', exact: true }).click();
    if (fresh) await page.getByRole('button', { name: '新对话', exact: true }).click();
    const prompt = readFileSync(promptPath, 'utf8');
    save(stage + '/prompt.txt', prompt);
    await page.locator('.chat-input').fill(prompt);
    const responsePromise = page.waitForResponse(r => r.request().method() === 'POST' &&
      /\/conversations(?:\/[^/]+\/turns)?$/.test(new URL(r.url()).pathname));
    await page.locator('.chat-input').press('Enter');
    const response = await responsePromise;
    const accepted = await response.json();
    save(stage + '/accepted.json', { http_status: response.status(), ...accepted });
    if (!response.ok()) throw new Error(JSON.stringify(accepted));
    await page.screenshot({ path: join(out, stage + '/submitted.png'), fullPage: true });
    console.log(JSON.stringify({ stage, accepted }));
  } finally { await browser.close(); }
}
async function wait(stage) {
  const run = read('run.json');
  const accepted = read(stage + '/accepted.json');
  const prefix = '/file-workspaces/' + run.workspace.id;
  const until = Date.now() + 20 * 60 * 1000;
  let lastPrint = 0;
  while (Date.now() < until) {
    const snapshot = await api(prefix + '/conversations/' + accepted.conversation_id + '/messages');
    const events = snapshot.events.filter(e => e.turn_id === accepted.turn_id);
    save(stage + '/raw-events.json', snapshot, true);
    const final = events.find(e => e.type === 'run_finished');
    if (Date.now() - lastPrint >= 15000 || final) {
      console.log(JSON.stringify({ stage, events: events.length, requests: events.filter(e => e.type === 'model_request_started').length,
        tools: events.filter(e => e.type === 'tool_started').map(e => e.name ?? e.tool ?? e.summary), final: final ?? null }));
      lastPrint = Date.now();
    }
    if (final) {
      const compact = events.filter(e => !['assistant_block_delta', 'assistant_block_started', 'assistant_block_finished'].includes(e.type));
      save(stage + '/events.json', compact);
      save(stage + '/catalog.json', await api(prefix + '/files'));
      save(stage + '/git.json', await api(prefix + '/git/log'));
      console.log('FINISHED ' + stage);
      return;
    }
    await new Promise(r => setTimeout(r, 4000));
  }
  await api(prefix + '/turns/' + accepted.turn_id + '/stop', {});
  throw new Error('Observed timeout; stopped task, no retry');
}
const domainMap = { chapters: 'chapter_index', characters: 'disclosure', timeline: 'timeline', clues: 'clues', glossary: 'glossary' };
async function capture(stage) {
  const run = read('run.json');
  const prefix = '/file-workspaces/' + run.workspace.id;
  const catalog = await api(prefix + '/files');
  save(stage + '/catalog.json', catalog);
  const files = [];
  for (const entry of [...catalog.chapters, ...catalog.characters, ...catalog.settings]) {
    if (!entry.exists) continue;
    const file = await api(prefix + '/file?path=' + encodeURIComponent(entry.path));
    save(stage + '/files/' + entry.path, file.text);
    files.push({ path: entry.path, digest: file.digest, chars: file.text.length });
  }
  save(stage + '/file-versions.json', files);
  const { browser, page } = await browserPage();
  try {
    save(stage + '/writing.txt', await page.locator('body').innerText());
    await page.screenshot({ path: join(out, stage + '/writing.png'), fullPage: true });
    for (const [section, domain] of Object.entries(domainMap)) {
      const data = await api(prefix + '/story-materials/' + domain);
      save(stage + '/' + domain + '.json', data);
      const first = data.entries[0];
      const route = `#/workspaces/${run.workspace.id}/materials/${section}` + (first ? '?entry=' + first.id : '');
      // Wait for the page's own author-view response instead of guessing at DOM timing.
      const loaded = page.waitForResponse(r => r.url().includes('/story-materials/' + domain) && r.status() === 200, { timeout: 30000 });
      await page.goto(base + '/' + route);
      await page.getByRole('button', { name: '重新读取资料', exact: true }).waitFor();
      await loaded;
      await page.waitForTimeout(600);
      const body = await page.locator('body').innerText();
      save(stage + '/' + section + '.txt', body);
      await page.screenshot({ path: join(out, stage + '/' + section + '.png'), fullPage: true });
      const buttons = await page.getByRole('button').evaluateAll(nodes => nodes.map(n => ({ text: n.innerText, disabled: n.disabled })));
      save(stage + '/' + section + '-buttons.json', buttons);
      console.log(JSON.stringify({ stage, section, entries: data.entries.length, coverage: data.coverage,
        actions: buttons.filter(x => /整理|核对/.test(x.text)) }));
    }
    save(stage + '/roster.json', await api(prefix + '/story-materials/roster'));
  } catch (error) {
    save(stage + '/capture-error.txt', String(error) + '\n' + await page.locator('body').innerText());
    await page.screenshot({ path: join(out, stage + '/capture-error.png'), fullPage: true });
    throw error;
  } finally { await browser.close(); }
}
async function organize(stage, domain, entityNames = []) {
  const run = read('run.json');
  const section = Object.keys(domainMap).find(s => domainMap[s] === domain) ?? 'characters';
  const { browser, page } = await browserPage();
  try {
    await page.goto(`${base}/#/workspaces/${run.workspace.id}/materials/${section}`);
    const actionLabel = { chapter_index: '整理所选章节', disclosure: '核对身份披露', roster: '从章节整理人物状态',
      timeline: '从章节整理时间线', clues: '从章节整理线索', glossary: '从章节整理名词' }[domain];
    await page.getByRole('button', { name: actionLabel, exact: true }).click();
    const dialog = page.getByRole('dialog');
    if (entityNames.length) {
      const people = dialog.locator('section').first();
      for (const input of await people.getByRole('checkbox').all()) await input.uncheck();
      for (const name of entityNames) await people.getByRole('checkbox', { name, exact: true }).check();
    }
    const responsePromise = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname.endsWith('/material-tasks'));
    await dialog.getByRole('button', { name: /^(整理所选章节|从章节整理时间线|从章节整理线索|从章节整理名词|开始核对|开始整理)$/ }).click();
    const response = await responsePromise;
    const accepted = await response.json();
    save(stage + '/accepted.json', { http_status: response.status(), ...accepted });
    save(stage + '/request.json', response.request().postDataJSON());
    if (!response.ok()) throw new Error(JSON.stringify(accepted));
    console.log(JSON.stringify({ stage, accepted }));
  } finally { await browser.close(); }
}
if (action === 'start') await start();
else if (action === 'send') await send(args[0], args[1]);
else if (action === 'wait') await wait(args[0]);
else if (action === 'capture') await capture(args[0]);
else if (action === 'organize') await organize(args[0], args[1], args.slice(2));
else if (action === 'inspect') {
  const { browser, page } = await browserPage();
  const errors = [];
  const responses = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', e => { if(e.type() === 'error') errors.push(e.text()); });
  page.on('response', r => { if(r.url().includes('story-materials') || r.url().includes('material-tasks')) responses.push({url:r.url(),status:r.status()}); });
  try {
    await page.goto(`${base}/#/workspaces/${read('run.json').workspace.id}/materials/${args[0]}`);
    await page.getByRole('button', {name:'重新读取资料', exact:true}).waitFor();
    // Active chat SSE intentionally prevents networkidle. Inspect the settled document directly.
    await page.waitForFunction(() => document.querySelector('[aria-label="查看截至第几章"]')?.textContent.includes('（第'), {timeout:10000}).catch(e => errors.push(String(e)));
    const body = await page.locator('body').innerText();
    save('diagnostic-' + args[0] + '.json', {url:page.url(),body,errors,responses});
    await page.screenshot({path:join(out,'diagnostic-' + args[0] + '.png'),fullPage:true});
    console.log(JSON.stringify({url:page.url(),body,errors,responses}));
  } finally { await browser.close(); }
}
else throw new Error('Unknown action');
