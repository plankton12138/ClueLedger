import { test as base, expect } from "@playwright/test";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { resolve, join } from "node:path";
import { execFileSync } from "node:child_process";

const parent = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "books");

export const test = base.extend<{ book: { id: string; path: string; otherId: string; moduleId: string; revisionId: string; body: string } }>({
  book: async ({ request }, use) => {
    mkdirSync(parent, { recursive: true });
    const root = mkdtempSync(join(parent, "experience-"));
    async function api(path: string, data: unknown) {
      const response = await request.post(path, { data });
      expect(response.ok(), await response.text()).toBeTruthy();
      return response.json();
    }
    const other = await api("/api/file-workspaces", { path: join(root, "空作品"), create: true });
    const book = await api("/api/file-workspaces", { path: join(root, "雨夜与港口的长标题隔离样例"), create: true });
    writeFileSync(join(book.path, "正文", "0001 雨夜.md"), "# 雨夜\n\n侦探抵达港口，雨仍未停。\n".repeat(100), "utf8");
    execFileSync("uv", ["run", "--directory", "..", "python", resolve("e2e/fixtures/frontend_experience.py"), book.path,
      resolve(parent, "..", "state", "workspaces", book.id)], { windowsHide: true, stdio: "pipe" });
    const body = "未停的雨🌧️与侦探𠮷。".repeat(1700);
    const created = await api("/api/module-library/modules", {
      kind: "reference", name: `长记录-${book.id}`, request_key: `create-${book.id}`,
      entries: [{ entry_id: "rain", title: "雨夜原文", body }, { entry_id: "port", title: "港口", body: "潮汐表。" }],
    });
    await api(`/api/module-library/modules/${created.module.module_id}/revisions`, {
      base_revision: created.module.head, expected_head: created.module.head, request_key: `rev-${book.id}`,
      entries: [{ entry_id: "rain", title: "雨夜原文", body }, { entry_id: "port", title: "港口", body: "潮汐表第二版。" }],
    });
    await api("/api/module-library/modules", { kind: "world", name: `海上城市-${book.id}`, request_key: `world-${book.id}`, entries: [{ entry_id: "city", title: "城市", body: "终年起雾。" }] });
    await api(`/api/file-workspaces/${book.id}/ideas`, { title: "旧识", body: "侦探认出了雨衣。", tags: [], request_key: `idea-${book.id}` });
    await use({ id: book.id, path: book.path, otherId: other.id, moduleId: created.module.module_id, revisionId: created.module.head, body });
  },
});
export { expect };
