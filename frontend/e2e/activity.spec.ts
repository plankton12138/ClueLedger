import { test, expect } from "@playwright/test";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("在线模型任务的事件流、输入详情、跨轮实时更新、筛选与导出", async ({ page, request }, info) => {
  test.setTimeout(240_000);
  const configResponse = await request.get("/api/model-connections");
  expect(configResponse.ok()).toBeTruthy();
  const config = await configResponse.json();
  const connection = config.connections.find((item: { name: string }) => item.name === config.selection.connection_id);
  expect(connection, "验收需要在线连接").toBeTruthy();
  expect(["localhost", "127.0.0.1", "[::1]", "0.0.0.0"]).not.toContain(new URL(connection.base_url).hostname);
  await info.attach("model", { body: JSON.stringify({ connection: connection.name, model: config.selection.model }), contentType: "application/json" });
  const root = mkdtempSync(join(tmpdir(), "hastings-activity-"));
  const opened = await request.post("/api/file-workspaces", { data: { path: root, create: true } });
  expect(opened.ok()).toBeTruthy();
  const workspace = (await opened.json()).id;
  const base = `/api/file-workspaces/${workspace}`;
  const chapter = "正文/0001 雨夜.md";
  writeFileSync(join(root, chapter), "# 雨夜\n\n窗外下着雪。侦探走进旅馆。\n", "utf-8");
  expect((await request.post(base + "/git/record", { data: { message: "事件流验收基线" } })).ok()).toBeTruthy();

  await page.goto("/");
  await page.getByLabel("选择作品目录").selectOption(workspace);
  await page.getByRole("button", { name: "活动记录", exact: true }).click();
  const panel = page.getByRole("dialog", { name: "活动记录" });
  await expect(panel.getByText("实时连接中", { exact: true })).toBeVisible();
  await panel.getByLabel("保存后续模型输入快照").check();
  await expect.poll(async () => (await (await request.get(base + "/activity/settings")).json()).capture_inputs).toBe(true);
  await panel.getByRole("button", { name: "关闭", exact: true }).click();

  await page.getByRole("button", { name: "新对话", exact: true }).click();
  await page.locator(".chat-input").fill(`把第一章《雨夜》中的「窗外下着雪」改成「窗外下着雨」。只修改 ${chapter}，其他文件不要动。`);
  await page.locator(".chat-input").press("Enter");
  await page.getByRole("button", { name: "活动记录", exact: true }).click();
  // 发送受理可能尚未刷新会话列表，作品范围能可靠订阅后续任务。
  await panel.getByLabel("活动范围").selectOption("");
  await expect(panel.getByText(/模型请求 ·/).first()).toBeVisible({ timeout: 180_000 });
  await expect(panel.getByText(/本轮结束 ·/).first()).toBeVisible({ timeout: 180_000 });
  const result = await (await request.get(base + "/activity?limit=500")).json();
  const details = await Promise.all(result.items.map(async (item: { event_id: string }) =>
    (await request.get(base + "/activity/" + encodeURIComponent(item.event_id))).json()));
  await info.attach("events", { body: JSON.stringify(details, null, 2), contentType: "application/json" });
  const terminal = details.find(item => item.type === "run_finished");
  expect(terminal?.status).toBe("completed");
  expect(details.some(item => item.type === "file_changed" && item.changed)).toBe(true);
  expect(details.some(item => item.type === "model_request_finished" && item.status === "completed")).toBe(true);
  expect(details.some(item => item.type === "tool_started" && item.parent_operation_id)).toBe(true);
  expect(details.some(item => item.type === "version_finished" && item.commit)).toBe(true);
  const file = await (await request.get(base + "/file", { params: { path: chapter } })).json();
  expect(file.text).toContain("窗外下着雨");

  const firstRequest = panel.getByRole("button").filter({ hasText: /模型请求 ·/ }).first();
  await firstRequest.click();
  await panel.getByRole("button", { name: "查看输入详情", exact: true }).click();
  await expect(panel.locator("pre").last()).toContainText("system");
  await expect(panel.locator("pre").last()).toContainText("窗外下着雪");
  await firstRequest.click();

  await panel.getByLabel("事件类型").selectOption("file");
  await expect(panel.getByText(/文件变化 ·/).first()).toBeVisible();
  await panel.getByLabel("搜索活动").fill(chapter);
  await expect(panel.getByText(/文件变化 ·/).first()).toBeVisible();
  const downloaded = page.waitForEvent("download");
  await panel.getByRole("button", { name: "导出事件日志" }).click();
  const download = await downloaded;
  expect(download.suggestedFilename()).toBe("activity.jsonl");
  const stream = await download.createReadStream();
  let content = "";
  for await (const chunk of stream!) content += chunk.toString();
  expect(content).toContain("file_changed");
  expect(content).not.toContain('"type": "model_request_started"');

  await panel.getByLabel("搜索活动").fill("");
  await panel.getByLabel("事件类型").selectOption("");
  // 活动订阅不在 run_finished 结束；同一连接继续收到下一轮任务。
  const next = await request.post(`${base}/conversations/${terminal.conversation_id}/turns`, {
    data: { text: "只回复：已收到。不要读写文件。", request_key: `activity-${Date.now()}` },
  });
  expect(next.status()).toBe(202);
  await expect(panel.getByText(/本轮结束 ·/)).toHaveCount(2, { timeout: 180_000 });
  await page.screenshot({ path: info.outputPath("activity.png"), fullPage: true });
  await panel.getByRole("button", { name: "文件版本", exact: true }).click();
  await expect(panel.getByRole("heading", { name: "历史", exact: true })).toBeVisible();
});
