/**
 * V08 浏览器走查：模块库抽屉（浏览、选中版本、装入作品、窄窗口）。
 *
 * 运行前置：
 *   1. frontend/ 已 npm run build；
 *   2. 服务已启动，带一个空状态目录与临时库目录：
 *        uv run python -m hastings.server --port 8142 \
 *          --state-dir <空状态目录> --library-dir <临时库目录>
 * 用法：node e2e/walkthrough-v08-library.mjs
 *
 * 脚本通过真实接口准备作品与库实例，再只用界面完成装入；不调用模型。
 * 证据归档：dev-notes/v0.8/artifacts/walkthrough-v08/。
 */

import { chromium } from "playwright";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE = process.env.HASTINGS_WALKTHROUGH_BASE ?? "http://127.0.0.1:8142";
const SHOTS = join(HERE, "..", "..", "dev-notes", "v0.8", "artifacts", "walkthrough-v08");
const WORKSPACE = join(HERE, "..", "..", "data", "test-tmp", "walkthrough-v08-book");
const TMP = resolve(HERE, "..", "..", "data", "test-tmp");
const DERIVED = join(TMP, "另一条线");

mkdirSync(SHOTS, { recursive: true });

async function api(path, init) {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) throw new Error(`${path} 失败：${response.status} ${await response.text()}`);
  return response.json();
}

async function prepare() {
  rmSync(WORKSPACE, { recursive: true, force: true });
  rmSync(DERIVED, { recursive: true, force: true });
  const binding = await api("/api/file-workspaces", {
    method: "POST",
    body: JSON.stringify({ path: WORKSPACE, create: true }),
  });
  writeFileSync(join(WORKSPACE, "正文", "0001 雨夜.md"), "# 雨夜\n\n雪压住了山路。\n", "utf-8");

  const characters = await api("/api/module-library/modules", {
    method: "POST",
    body: JSON.stringify({
      kind: "characters",
      name: "主角团",
      request_key: "walkthrough-characters",
      entries: [
        { entry_id: "lin", title: "林静", body: "法医，与死者相识。", fields: { roles: ["suspect"] } },
      ],
    }),
  });
  const world = await api("/api/module-library/modules", {
    method: "POST",
    body: JSON.stringify({
      kind: "world",
      name: "海上城市",
      request_key: "walkthrough-world",
      entries: [
        { entry_id: "port", title: "港口", body: "终年起雾，进出口都走潮汐表。" },
      ],
    }),
  });
  const detail = await api(
    `/api/module-library/modules/${characters.module.module_id}/revisions/${characters.revision.revision_id}`,
  );
  await api(`/api/file-workspaces/${binding.id}/assembly`, {
    method: "PUT",
    body: JSON.stringify({
      bindings: [
        {
          label: "主角团",
          module: {
            library_id: detail.library_id,
            module_id: characters.module.module_id,
            revision_id: characters.revision.revision_id,
          },
        },
      ],
      expected_digest: null,
      request_key: "walkthrough-bind-1",
    }),
  });
  return { workspaceId: binding.id, characters, world };
}

async function main() {
  const prepared = await prepare();
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });

  await page.goto(BASE);
  await page.getByRole("button", { name: "打开 / 新建" }).waitFor({ timeout: 15_000 });
  // 作品是通过接口建的：页面重新读一次登记，再用下拉框选中它。
  await page.reload();
  const picker = page.locator('select[aria-label="选择作品目录"]');
  await picker.selectOption({ label: "walkthrough-v08-book" }, { timeout: 15_000 });
  console.log("已选中作品");
  await page.getByRole("button", { name: "模块库", exact: true }).click();
  console.log("已打开抽屉");
  const drawer = page.getByRole("dialog", { name: "模块库" });
  await drawer.waitFor();
  await drawer.getByText("主角团").first().waitFor();
  await page.screenshot({ path: join(SHOTS, "00-overview.png"), fullPage: false });
  await drawer.getByRole("button", { name: /主角团/ }).last().click();
  await drawer.locator('input[aria-label="条目标题"]').first().waitFor({ timeout: 15_000 });
  await page.screenshot({ path: join(SHOTS, "01-detail.png"), fullPage: false });

  // 选中世界设定并装入当前作品；已装列表随即出现两份内容。
  await drawer.getByRole("button", { name: /海上城市/ }).first().click();
  // 条目标题是输入框的 value，不是文本节点；等编辑区出现即可。
  await drawer.locator('input[aria-label="条目标题"]').first().waitFor({ timeout: 15_000 });
  await drawer.getByRole("button", { name: "装入本作品" }).click();
  await drawer.getByText(/已装入本作品/).waitFor();
  await drawer.getByText("本作品已装（2）").waitFor();
  await page.screenshot({ path: join(SHOTS, "02-bound.png"), fullPage: false });

  // 用当前组合派生一个新作品：抽屉关闭，顶栏切到新作品，正文为空。
  await drawer.getByRole("button", { name: "用当前组合新建作品" }).click();
  await drawer.getByLabel("新作品名称").fill("另一条线");
  await drawer.getByRole("button", { name: "创建", exact: true }).click();
  await page.waitForFunction(
    () => {
      const select = document.querySelector('select[aria-label="选择作品目录"]');
      return Boolean(
        select &&
          Array.from(select.selectedOptions).some((option) =>
            (option.textContent ?? "").includes("另一条线"),
          ),
      );
    },
    { timeout: 15_000 },
  );
  await page.screenshot({ path: join(SHOTS, "04-derived.png"), fullPage: false });
  await page.getByRole("button", { name: "模块库", exact: true }).click();
  await drawer.waitFor();
  // 新作品继承了同一组模块；重新选中一份，确认详情可读。
  await drawer.getByRole("button", { name: /主角团/ }).last().click();
  await drawer.locator('input[aria-label="条目标题"]').first().waitFor({ timeout: 15_000 });

  // 窄窗口：抽屉铺满，左右两栏仍可用。
  await page.setViewportSize({ width: 1024, height: 820 });
  await page.screenshot({ path: join(SHOTS, "03-narrow.png"), fullPage: false });

  // 界面上的保存按钮不应在非最新版本上可用；这里只做一次可见性检查。
  const save = drawer.getByRole("button", { name: "保存到模块库" });
  if (!(await save.isVisible())) throw new Error("保存按钮没有出现在详情里");

  await drawer.getByRole("button", { name: "关闭" }).click();
  await browser.close();
  if (errors.length > 0) {
    throw new Error(`浏览器控制台报错：\n${errors.join("\n")}`);
  }
  console.log(
    `走查完成：${prepared.workspaceId}；截图 ${join(SHOTS, "00-overview.png")} 等 5 张`,
  );
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
