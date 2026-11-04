import { expect, test, type APIRequestContext } from "@playwright/test";
import { mkdirSync, mkdtempSync } from "node:fs";
import { join, resolve } from "node:path";

/**
 * v1.2 在线冒烟：只保留一条真实模型链路，避免短时间重复调用。
 *
 * 作者只在对话中给自然语言；模型负责建立 STRUCTURE.md。页面不得出现作品意图、
 * 正文编辑器或另一个创建入口。具体回复措辞不作为断言。
 */
test.describe.configure({ mode: "serial" });
test.setTimeout(300_000);

const parent = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "books");
mkdirSync(parent, { recursive: true });

async function newBook(request: APIRequestContext) {
  const root = join(mkdtempSync(join(parent, "online-writing-")), "雪夜旅馆");
  const opened = await request.post("/api/file-workspaces", {
    data: { path: root, create: true },
  });
  expect(opened.ok(), await opened.text()).toBeTruthy();
  const book = await opened.json();
  return { id: book.id as string, base: `/api/file-workspaces/${book.id}` };
}

test("自然语言从对话建立全书结构，页面没有平行创作入口", async ({ page, request }) => {
  const book = await newBook(request);
  await page.goto(`/#/workspaces/${book.id}/writing`);

  await expect(page.getByRole("textbox", { name: "作品意图" })).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "正文编辑" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /新建章节|导入正文|添加模块/ })).toHaveCount(0);

  const input = page.locator(".chat-input");
  await input.fill(
    "请直接建立这个作品：暴雪封山的旅馆里少了一名住客，侦探从一只湿手套开始调查。未知的真相先保持未知。",
  );
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator('[data-role="assistant"]').last()).toBeVisible({ timeout: 240_000 });

  await expect.poll(async () => {
    const response = await request.get(`${book.base}/file`, {
      params: { path: "STRUCTURE.md" },
    });
    return response.ok() ? (await response.json()).text as string : "";
  }, { timeout: 240_000, intervals: [2_000, 5_000, 10_000] }).toContain("## 铁律");

  await page.getByRole("button", { name: "全书结构", exact: true }).click();
  await expect(page.getByRole("heading", { name: "全书结构", exact: true })).toBeVisible();
  await expect(page.getByText("暴雪封山")).toBeVisible();
});
