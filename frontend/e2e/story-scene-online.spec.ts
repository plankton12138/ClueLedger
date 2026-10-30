import { test, expect } from "@playwright/test";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

test("真实候选保存、节点定位、试读与成组撤销", async ({ page, request }, info) => {
  test.setTimeout(420_000);
  page.setDefaultTimeout(30_000);
  const config = await request.get("/api/model-connections").then(response => response.json());
  expect(config.selection.available).toBe(true);
  const parent = resolve(process.env.HASTINGS_E2E_DATA_DIR!, "scene-books");
  mkdirSync(parent, { recursive: true });
  const root = join(mkdtempSync(join(parent, "scene-")), "雪夜");
  const created = await request.post("/api/file-workspaces", { data: { path: root, create: true } });
  expect(created.ok()).toBeTruthy();
  const book = await created.json();
  const base = `/api/file-workspaces/${book.id}`;
  const path = "正文/0001 雨夜.md";
  const original = "# 雨夜\n\n林砚抵达旅馆。柜台上一双湿手套，手套还在滴水。管家说自己一直在房间。\n";
  writeFileSync(join(root, path), original, "utf8");
  const seeded = await request.post(`${base}/story/changes`, { data: {
    variant_id: "main", expected_revision: "empty", request_key: "scene-seed", operations: [
      { op: "add_clue", clue: { id: "glove", title: "湿手套", author_truth: "管家在雪停后搬过尸体" } },
      { op: "add_node", node: { id: "arrival", title: "抵达", action: "林砚看到湿手套", clue_uses: [{ clue_id: "glove", presentation: "手套还在滴水", mode: "narration" }] } },
    ],
  } });
  expect(seeded.ok()).toBeTruthy();
  await page.goto(`/#/workspaces/${book.id}/writing`);
  const surface = page.getByRole("region", { name: "构思结构" });
  await expect(page.getByRole("button", { name: "构思", exact: true })).toBeVisible();
  if (!await surface.isVisible()) await page.getByRole("button", { name: "构思", exact: true }).click();
  await surface.getByRole("button", { name: /^抵达(?:\s|$)/ }).click();
  await surface.getByRole("button", { name: "引用到对话", exact: true }).click();
  await page.locator(".chat-input").fill(`修改${path}：保留原标题，扩写为两三段完整的抵达场景，让林砚与管家有实际行动和言语冲突，自然呈现手套还在滴水。林砚不能知道搬尸真相。只改这一文件，先读原文、提出候选、核对后保存。`);
  const acceptedPromise = page.waitForResponse(response => response.status() === 202 && response.request().method() === "POST" && /\/(conversations|turns)$/.test(new URL(response.url()).pathname));
  await page.getByRole("button", { name: "发送", exact: true }).click();
  const accepted = await (await acceptedPromise).json();
  let events: { type: string; seq: number; text?: string; status?: string }[] = [];
  await expect.poll(async () => {
    events = (await request.get(`${base}/conversations/${accepted.conversation_id}/messages`).then(response => response.json())).events;
    return events.find(event => event.type === "run_finished")?.status;
  }, { timeout: 240_000 }).toBe("completed");
  const delta = events.find(event => event.type === "assistant_block_delta");
  expect(delta).toBeTruthy();
  expect(delta!.seq).toBeLessThan(events.find(event => event.type === "run_finished")!.seq);
  const saved = await request.get(`${base}/file`, { params: { path } }).then(response => response.json());
  expect(saved.text).not.toBe(original);
  expect(saved.text).not.toContain("搬过尸体");
  await expect(surface.locator(".story-binding")).toHaveCount(1);
  await surface.locator(".story-binding").click();
  await expect(page.getByRole("button", { name: "返回构思", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "返回构思", exact: true }).click();
  await surface.getByRole("button", { name: /^抵达(?:\s|$)/ }).click();
  await surface.getByRole("button", { name: "试读", exact: true }).click();
  const reader = page.getByRole("dialog", { name: "试读", exact: true });
  const previewPromise = page.waitForResponse(response => response.status() === 202 && response.url().endsWith("/reader-previews"));
  await reader.getByRole("button", { name: "试读到所选节点" }).click();
  const preview = await (await previewPromise).json();
  await expect.poll(async () => (await request.get(`/api/operations/${preview.operation_id}`).then(response => response.json())).status, { timeout: 90_000 }).toBe("completed");
  await expect(reader).toContainText("手套");
  await page.keyboard.press("Escape");
  await surface.getByRole("button", { name: "撤销上一次", exact: true }).click();
  await expect.poll(async () => (await request.get(`${base}/file`, { params: { path } }).then(response => response.json())).text).toBe(original);
  await page.screenshot({ path: info.outputPath("scene-undo.png") });
  await info.attach("scene-events", { body: JSON.stringify(events), contentType: "application/json" });
});
