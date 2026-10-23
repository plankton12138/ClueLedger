import { afterEach, describe, expect, it, vi } from "vitest";
import { activityPage, activityRequest, activityStream, activityUrl, exportActivity,
  mergeActivity, type ActivityEvent, type ActivityPage } from "./activity";

afterEach(() => vi.unstubAllGlobals());

const event = (id: number, at = `2026-10-23T04:00:${String(id).padStart(2, "0")}Z`): ActivityEvent => ({
  id, event_id: `e${id}`, at, conversation_id: "c1", turn_id: "t1", type: "tool_finished",
  category: "tool", status: "completed", action: "tool", phase: "completed", title: "读取",
  subject: "", note: "", pair_key: `tool:${id}`, summary: "读取第一章",
});
const page: ActivityPage = { items: [event(1)], cursor: "a:1", reset: false, has_more: false,
  next_before: null, stats: { events: 1, models: 0, tools: 1, errors: 0 } };

describe("活动记录客户端", () => {
  it("将文件名、搜索与范围编码为查询参数", () => {
    const url = new URL(activityUrl("book", "", { q: "正文/第一章.md & 雨", verbose: false }), "http://local");
    expect(url.searchParams.get("q")).toBe("正文/第一章.md & 雨");
    expect(url.searchParams.get("verbose")).toBe("false");
  });

  it("分页保留筛选范围并传递取消信号", async () => {
    const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(page)));
    vi.stubGlobal("fetch", mock);
    const controller = new AbortController();
    await activityPage("book", { conversation_id: "c1", category: "tool" }, 120, controller.signal);
    const [url, init] = mock.mock.calls[0]!;
    expect(String(url)).toContain("before=120");
    expect(String(url)).toContain("conversation_id=c1");
    expect(init.signal).toBe(controller.signal);
  });

  it("回读与实时重复事件去重，按实际时间排序，索引重建清除旧页面", () => {
    const current = [event(1), event(2)];
    expect(mergeActivity(current, [event(2), event(3)]).map(e => e.id)).toEqual([1, 2, 3]);
    expect(mergeActivity(current, [event(4, "2026-10-23T03:00:00Z")]).map(e => e.id)).toEqual([4, 1, 2]);
    expect(mergeActivity(current, [event(5)], true).map(e => e.id)).toEqual([5]);
    expect(current).toHaveLength(2);
  });

  it("跨字节分片解析中文 SSE，并保留续传游标", async () => {
    const bytes = new TextEncoder().encode(`id: a:1\r\ndata: ${JSON.stringify(page)}\r\n\r\n`);
    let cancelled = false;
    const stream = new ReadableStream({
      start(controller) { for (let i = 0; i < bytes.length; i += 7) controller.enqueue(bytes.slice(i, i + 7)); },
      cancel() { cancelled = true; },
    });
    const mock = vi.fn().mockResolvedValue(new Response(stream)); vi.stubGlobal("fetch", mock);
    const iter = activityStream("book", { category: "tool" }, "a:0", new AbortController().signal);
    expect((await iter.next()).value).toEqual(page);
    expect(String(mock.mock.calls[0]![0])).toContain("after=a%3A0");
    await iter.return(undefined);
    expect(cancelled).toBe(true);
  });

  it("导出沿用筛选，不只导出当前已加载的行", async () => {
    const mock = vi.fn().mockResolvedValue(new Response("{\"detail\":1}\n"));
    vi.stubGlobal("fetch", mock);
    const blob = await exportActivity("book", { q: "第一章", status: "failed" });
    expect(await blob.text()).toContain("detail");
    expect(String(mock.mock.calls[0]![0])).toContain("/export?");
    expect(String(mock.mock.calls[0]![0])).not.toContain("before=");
  });

  it("服务端错误显示原错误，不把错误响应导出成成功文件", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => new Response(JSON.stringify({
      error: { code: "ACTIVITY_STORE_FAILED", why: "目录不可写", fix: "检查权限" },
    }), { status: 500 })));
    await expect(activityRequest("book")).rejects.toMatchObject({ code: "ACTIVITY_STORE_FAILED" });
    await expect(exportActivity("book", {})).rejects.toThrow("目录不可写");
  });
});
