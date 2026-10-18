/** Render the editable Mermaid sources. See README.md for prerequisites. */
import { readFile, readdir, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../../..');
const mermaidPath = process.argv[2];
if (!mermaidPath) {
  throw new Error('Usage: node product-design/diagrams/harness/render.mjs <mermaid.min.js> [preview-directory]');
}
const previewDir = process.argv[3] && path.resolve(process.argv[3]);
if (previewDir) await mkdir(previewDir, { recursive: true });
const { chromium } = await import(pathToFileURL(path.join(root, 'frontend/node_modules/playwright/index.mjs')).href);
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
  await page.setContent('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head><body style="margin:0;background:white"></body></html>');
  await page.addScriptTag({ path: path.resolve(mermaidPath) });
  await page.evaluate(() => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      htmlLabels: false,
      theme: 'base',
      fontFamily: 'Microsoft YaHei, Noto Sans CJK SC, Arial, sans-serif',
      themeVariables: {
        fontFamily: 'Microsoft YaHei, Noto Sans CJK SC, Arial, sans-serif',
        fontSize: '17px', primaryColor: '#edf5ff', primaryTextColor: '#183650',
        primaryBorderColor: '#4677a7', lineColor: '#667c8a',
        secondaryColor: '#eaf5f0', tertiaryColor: '#fff4df',
        clusterBkg: '#f8fafc', clusterBorder: '#c9d4dc',
        edgeLabelBackground: '#ffffff', actorBkg: '#edf5ff',
        actorBorder: '#4677a7', actorTextColor: '#183650',
        signalColor: '#334e62', signalTextColor: '#183650',
        noteBkgColor: '#fff4df', noteBorderColor: '#ae8037', noteTextColor: '#654719',
      },
      flowchart: { htmlLabels: false, curve: 'linear', nodeSpacing: 32, rankSpacing: 42, padding: 18, wrappingWidth: 300, useMaxWidth: false },
      sequence: { useMaxWidth: false, actorMargin: 40, messageMargin: 26, noteMargin: 14, wrap: true, mirrorActors: false },
    });
  });
  const sources = (await readdir(here)).filter(name => name.endsWith('.mmd')).sort();
  for (const name of sources) {
    const source = await readFile(path.join(here, name), 'utf8');
    const { svg, width, height } = await page.evaluate(async ({ source }) => {
      const rendered = await mermaid.render('architecture', source);
      document.body.innerHTML = rendered.svg;
      await document.fonts.ready;
      const element = document.querySelector('svg');
      const box = element.viewBox.baseVal;
      element.setAttribute('width', Math.ceil(box.width));
      element.setAttribute('height', Math.ceil(box.height));
      element.setAttribute('role', 'img');
      element.style.background = '#ffffff';
      return { svg: new XMLSerializer().serializeToString(element), width: Math.ceil(box.width), height: Math.ceil(box.height) };
    }, { source });
    await writeFile(path.join(here, name.replace('.mmd', '.svg')), svg + '\n');
    if (previewDir) {
      await page.setViewportSize({ width: Math.min(2000, Math.max(800, width)), height: 1000 });
      await page.locator('svg').screenshot({ path: path.join(previewDir, name.replace('.mmd', '.png')) });
    }
    console.log(`${name}: ${width} × ${height}`);
  }
} finally {
  await browser.close();
}
