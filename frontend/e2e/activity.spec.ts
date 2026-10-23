import { test, expect } from "@playwright/test";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("在线模型任务按任务分组、动作可读、详情与导出可用", async ({ page, request }, info) => {
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
  // 记录设置收在折叠区里，先展开再勾选。
  await panel.getByText("记录设置", { exact: true }).click();
  await panel.getByLabel("保存后续模型输入快照").check();
  await expect.poll(async () => (await (await request.get(base + "/activity/settings")).json()).capture_inputs).toBe(true);
  await panel.getByRole("button", { name: "关闭", exact: true }).click();

  await page.getByRole("button", { name: "新对话", exact: true }).click();
  await page.locator(".chat-input").fill(`把第一章《雨夜》中的「窗外下着雪」改成「窗外下着雨」。只修改 ${chapter}，其他文件不要动。`);
  await page.locator(".chat-input").press("Enter");
  await page.getByRole("button", { name: "活动记录", exact: true }).click();
  // 发送受理可能尚未刷新会话列表，作品范围能可靠订阅后续任务。
  await panel.getByLabel("活动范围").selectOption("");
  const band = panel.locator("main section").filter({ hasText: "模型回复" }).first();
  const header = band.locator("div").first();
  await expect(header).toContainText("写作", { timeout: 180_000 });
  await expect(header).toContainText("完成", { timeout: 180_000 });
  await expect(header).toContainText("个文件");

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

  // 列表带界面说法：动作、阶段、成对键与标题；开始与结束用同一个成对键。
  // 详情接口仍返回原始事件，不合成展示字段。
  const rows = result.items as {
    type: string; tool?: string; operation_id?: string;
    action: string; phase: string; title: string; pair_key: string;
  }[];
  const requestStarted = rows.find(item => item.type === "model_request_started")!;
  expect(requestStarted.action).toBe("model");
  expect(requestStarted.title).toBe("请求模型");
  expect(requestStarted.phase).toBe("started");
  expect(requestStarted.pair_key).toBe(`model:${requestStarted.operation_id}`);
  const requestFinished = rows.find(item => item.type === "model_request_finished")!;
  expect(requestFinished.pair_key).toBe(requestStarted.pair_key);
  const writeTool = rows.find(item => item.type === "tool_started" && item.tool === "write_file");
  if (writeTool) expect(writeTool.title).toBe("写入");

  const file = await (await request.get(base + "/file", { params: { path: chapter } })).json();
  expect(file.text).toContain("窗外下着雨");

  // 一行一次调用：展开后能看到开始与结束两条原始事件。
  const modelRow = band.getByRole("button").filter({ hasText: "模型回复" }).first();
  await modelRow.click();
  await expect(panel.getByRole("button", { name: "详情", exact: true }).first()).toBeVisible();
  await panel.getByRole("button", { name: "详情", exact: true }).first().click();
  await panel.getByRole("button", { name: "查看输入详情", exact: true }).click();
  await expect(panel.locator("pre").last()).toContainText("system");
  await expect(panel.locator("pre").last()).toContainText("窗外下着雪");
  await modelRow.click();

  // 侧栏动作分布按界面的行计数。
  const aside = panel.locator("aside").first();
  await expect(aside).toContainText("动作分布");
  await expect(aside.getByText("文件", { exact: true }).first()).toBeVisible();
  await expect(aside.getByText("模型", { exact: true }).first()).toBeVisible();

  const downloaded = page.waitForEvent("download");
  await panel.getByRole("button", { name: "导出事件日志" }).click();
  const download = await downloaded;
  expect(download.suggestedFilename()).toBe("activity.jsonl");
  const stream = await download.createReadStream();
  let content = "";
  for await (const chunk of stream!) content += chunk.toString();
  // 未勾选「导出包含已保存详情」时导出全部事件与附件引用，不带附件内容。
  expect(content).toContain("file_changed");
  expect(content).toContain("model_request_started");
  expect(content).not.toContain('"attachments"');

  // 活动订阅不在 run_finished 结束；同一连接继续收到下一轮任务，并按新任务单独分组。
  const next = await request.post(`${base}/conversations/${terminal.conversation_id}/turns`, {
    data: { text: "只回复：已收到。不要读写文件。", request_key: `activity-${Date.now()}` },
  });
  expect(next.status()).toBe(202);
  await expect(panel.locator("main section").filter({ hasText: "模型回复" })).toHaveCount(2, { timeout: 180_000 });
  await page.screenshot({ path: info.outputPath("activity.png"), fullPage: true });
  await panel.getByRole("button", { name: "文件版本", exact: true }).click();
  await expect(panel.getByRole("heading", { name: "历史", exact: true })).toBeVisible();
});
