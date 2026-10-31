/** 当前页面截图，不调用模型。先用隔离 state/library 启动服务。
 * node scripts/capture_readme_screenshots.cjs http://127.0.0.1:8961 data/test-tmp/readme-screenshots
 */
const { chromium, expect } = require('../frontend/node_modules/@playwright/test');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const base = process.argv[2] || 'http://127.0.0.1:8961';
const data = path.resolve(root, process.argv[3] || 'data/test-tmp/readme-screenshots');
const output = path.join(root, 'docs/screenshots/readme');

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
    async function api(route, body, method = 'POST') {
      const response = await page.request.fetch(base + route, { method, data: body });
      if (!response.ok()) throw new Error(`${route}: ${await response.text()}`);
      return response.json();
    }
    let book;
    if (process.argv[4]) {
      book = { id: process.argv[4] };
    } else {
      const libraryBefore = await page.request.get(`${base}/api/module-library/modules`).then(r => r.json());
      if (libraryBefore.items.length) throw new Error('请使用全新隔离 state/library 启动截图服务；不改动已有资料库。');
      book = await api('/api/file-workspaces', { path: path.join(data, `sample-${Date.now()}`, '雪夜来信'), create: true });
      execFileSync('uv', ['run', 'python', 'scripts/readme_fixture.py', book.path, path.join(data, 'state'), book.id], { cwd: root, windowsHide: true });
      const modules = [];
      for (const [name, body] of [
        ['人物与关系', '## 林岚\n\n收到一封没有署名的信，带着旧照片来到旅馆。她想知道是谁寄出的，又不愿让人知道照片背面的字。\n\n## 周远\n\n退休调查员。善于听别人省略了什么，先把每个人的说法记下来。\n\n## 旅馆老板\n\n知道十三年前的旧事，一直避免提起那场雪。'],
        ['雪夜旅馆', '## 封闭条件\n\n末班车离开后山路封闭，电话时断时续，所有来客都要留到雪停。\n\n## 空间安排\n\n一楼是门厅、餐厅和壁炉，二楼走廊尽头有一扇没有关严的窗。第三阶木板会响。'],
        ['线索与回收计划', '## 没有署名的信\n\n把原本陌生的来客联系起来。\n\n## 停住的钟\n\n记录可信，最初对时间的解释却可能有误。\n\n## 没有响的木阶\n\n熟悉旅馆的人知道如何避开第三阶木板。'],
      ]) {
        const created = await api('/api/module-library/modules', { kind: 'reference', name, entries: [{ entry_id: 'content', title: name, body }], request_key: `readme-${name}-${book.id}` });
        const detail = await page.request.get(`${base}/api/module-library/modules/${created.module.module_id}/revisions/${created.module.head}`).then(r => r.json());
        modules.push({ module: { library_id: detail.library_id, module_id: created.module.module_id, revision_id: created.module.head } });
      }
      await api(`/api/file-workspaces/${book.id}/assembly`, { bindings: modules, expected_digest: null, request_key: 'readme-assembly' }, 'PUT');
      for (const [title, body] of [
        ['钟还在走', '秒针没有停，为什么大家都记成了同一个时间？'],
        ['避开的木阶', '把开篇的楼梯声音，变成后面核对行踪的依据。'],
        ['来信的人', '每个人收到的信略有不同，恰好隐去了自己最在意的那件事。'],
      ]) await api(`/api/file-workspaces/${book.id}/ideas`, { title, body, tags: [], request_key: `idea-${title}` });
      const stages = ['雪夜封闭', '解释分歧', '相互推翻', '共同收束'];
      const lanes = ['调查推进', '人物隐瞒', '旧事追查'];
      const titles = [
        ['意外成为共同解释', '设备记录支持意外', '异常时间被重新解释'],
        ['证词留下空白', '隐瞒被误读成嫌疑', '保护行为推翻在场'],
        ['旧记录出现缺页', '追查旧事遭到阻挠', '旧事说明隐瞒动机'],
      ];
      const changes = [
        ['众人先接受意外的说法', '异常时间看似印证旧说', '记录可信，推断对象却错了'],
        ['一人避谈案发夜的行踪', '为保护另一人改动证词', '真实行踪改变调查方向'],
        ['起初与眼前案件无关', '当事人拒绝说明缺页', '解释了证词，尚未证明凶手'],
      ];
      const operations = [
        ...stages.map((title, order) => ({ op: 'add_stage', stage: { id: `s${order}`, title, order } })),
        ...lanes.map((title, order) => ({ op: 'add_thread', thread: { id: `t${order}`, title, order, goal: ['还原雪夜经过', '辨认隐瞒的原因', '补齐十三年前的记录'][order], obstacle: ['时间记录容易被误读', '证人各自有所保留', '关键一页已经遗失'][order] } })),
        ...['没有署名的信', '停住的钟', '没有响的木阶'].map((title, i) => ({ op: 'create_clue_identity', temp_id: `c${i}`, title })),
        ...[0, 1, 2].map(i => ({ op: 'upsert_clue_plan', clue_id: `c${i}`, plan: { intention: ['让来客的共同经历逐步显现', '让读者先信任记录，再重新理解时间', '用开篇细节核对后来声称的路线'][i], resolution_intent: 'explain', author_truth: null } })),
        ...[0, 1, 2].flatMap(t => [0, 1, 2].map(i => ({ op: 'add_node', node: { id: `n${t}${i}`, title: titles[t][i], action: changes[t][i] + '。核对现有证据与当事人的说法，让新信息改变下一步行动。', change: changes[t][i], stage_id: `s${i}`, thread_ids: [`t${t}`], primary_thread_id: `t${t}`, clue_uses: [{ clue_id: `c${t}`, roles: [i === 0 ? 'plant' : 'develop'], presentation: changes[t][i], mode: 'narration' }] } }))),
        { op: 'add_node', node: { id: 'join', title: '三条线共同重建案发经过', action: '把时间记录、真实行踪与旧事放在一起核对，分别补足不同缺口。', change: '记录、真实行踪与旧事分别补足不同缺口', stage_id: 's3', thread_ids: ['t0', 't1', 't2'], primary_thread_id: 't0', is_convergence: true, clue_uses: [0, 1, 2].map(i => ({ clue_id: `c${i}`, roles: ['resolve'], presentation: '与其他证据共同解释雪夜的经过', mode: 'narration' })) } },
        ...[0, 1, 2].flatMap(t => [0, 1, 2].map(i => ({ op: 'connect', edge: { id: `e${t}${i}`, from_id: `n${t}${i}`, to_id: i === 2 ? 'join' : `n${t}${i + 1}`, kind: 'next', reason: '本条线取得的信息推动下一次判断' } }))),
      ];
      await api(`/api/file-workspaces/${book.id}/story/changes`, { variant_id: 'main', expected_revision: 'empty', request_key: 'readme-structure', operations });
    }
    await page.goto(`${base}/#/workspaces/${book.id}/writing`);
    await page.getByRole('button', { name: '第一章 雪夜来客', exact: true }).click();
    await expect(page.getByRole('article')).toContainText('最后一班车离开后');
    await expect(page.getByRole('log', { name: '讨论内容' })).toContainText('本轮仅讨论');
    fs.mkdirSync(output, { recursive: true });
    const shot = async name => { await page.evaluate(() => document.fonts.ready); await page.waitForTimeout(150); await page.screenshot({ path: path.join(output, `${name}.png`), animations: 'disabled' }); console.log(`captured ${name}`); };
    await shot('writing');
    await page.getByRole('button', { name: '灵感', exact: true }).click();
    await page.getByLabel('记录灵感', { exact: true }).fill('最后一封信没有收件人，却让所有人以为是寄给自己的。');
    await expect(page.locator('.idea-note').first()).toBeVisible();
    await shot('ideas');
    await page.keyboard.press('Escape');
    await page.goto(`${base}/#/library?workspace=${book.id}`);
    await expect(page.locator('.module-library-card')).toHaveCount(3);
    await page.locator('.module-library-card').filter({ hasText: '雪夜旅馆' }).locator('.module-library-card-name').click();
    await expect(page.getByText('空间安排', { exact: true }).last()).toBeVisible();
    await shot('module-library');
    await page.goto(`${base}/#/workspaces/${book.id}/writing`);
    await page.getByRole('button', { name: '构思', exact: true }).click();
    const surface = page.getByRole('region', { name: '构思结构' });
    await expect(surface.locator('.story-node')).toHaveCount(10);
    await page.getByRole('button', { name: '全书线索', exact: true }).click();
    await expect(page.getByRole('region', { name: '全书线索' })).toContainText('停住的钟');
    await shot('story-structure');
    await surface.getByRole('button', { name: '隐瞒被误读成嫌疑', exact: true }).click();
    const clues = page.getByRole('region', { name: '全书线索' });
    await clues.getByRole('button', { name: /^停住的钟/ }).click();
    await expect(clues.getByRole('heading', { name: '作者计划' })).toBeVisible();
    await page.waitForTimeout(300);
    await surface.getByRole('region', { name: '画布横向滚动' }).evaluate(el => { el.scrollLeft -= 46; });
    await surface.getByRole('region', { name: '画布纵向滚动' }).evaluate(el => { el.scrollTop += 120; });
    await shot('story-clues');
    fs.writeFileSync(path.join(data, 'capture.json'), JSON.stringify({ example: true, model_calls: 0, workspace_id: book.id, viewport: '1600x1000', screenshots: ['writing', 'ideas', 'module-library', 'story-structure', 'story-clues'] }, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
