/**
 * refetchTargets 剧本测试（P3 T9，D7）：写工具成功事件 → 精准 refetch 映射。
 * 呈现策略纯函数——「事件进、刷新目标出」，与 reducer 同一测试哲学。
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import type { EventFrame, ToolExecutionEndEventFrame } from "./types";
import { createWorkspace, fetchBible, fetchChapter, fetchChapters, fetchWorkspaces, refetchTargets } from "./rest";

function toolEnd(toolName: string, _args: Record<string, unknown>, isError = false): ToolExecutionEndEventFrame {
  void _args; // end 帧不带 args（args 只在 start/update 上），保留形参以贴近调用现场
  return {
    type: "tool_execution_end",
    toolCallId: "c1",
    toolName,
    result: { content: [] },
    isError,
  };
}

describe("refetchTargets", () => {
  const argsOf = (id: string) => (id === "c1" ? { entity_type: "character" } : undefined);

  it("bible_upsert 成功 → 按调用参数里的 entity_type 刷新账本", () => {
    expect(refetchTargets(toolEnd("bible_upsert", {}), argsOf)).toEqual(["character"]);
    expect(
      refetchTargets(toolEnd("bible_upsert", {}), () => ({ entity_type: "foreshadow" })),
    ).toEqual(["foreshadow"]);
  });

  it("bible_upsert 失败（isError）→ 不刷新（数据正确性由工具层守门）", () => {
    expect(refetchTargets(toolEnd("bible_upsert", {}, true), argsOf)).toEqual([]);
  });

  it("method_lock / method_unlock 成功 → 刷新 method", () => {
    expect(refetchTargets(toolEnd("method_lock", {}), argsOf)).toEqual(["method"]);
    expect(refetchTargets(toolEnd("method_unlock", {}), argsOf)).toEqual(["method"]);
  });

  it("调用参数缺失 / 未知 entity_type / 非写工具 / 非结束事件 → 空列表", () => {
    expect(refetchTargets(toolEnd("bible_upsert", {}), () => undefined)).toEqual([]);
    expect(refetchTargets(toolEnd("bible_upsert", {}), () => ({ entity_type: "nope" }))).toEqual([]);
    expect(refetchTargets(toolEnd("write_chapter", {}), argsOf)).toEqual([]);
    const start: EventFrame = {
      type: "tool_execution_start",
      toolCallId: "c1",
      toolName: "bible_upsert",
      args: {},
    };
    expect(refetchTargets(start, argsOf)).toEqual([]);
  });
});

// ---------- 工作区面（P4 T6）：fetch 客户端剧本（stub 掉传输层） ----------

describe("workspace API（P4 T6）", () => {
  afterEach(() => vi.unstubAllGlobals());

  function jsonResponse(payload: unknown, status = 200): Response {
    return new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }

  it("fetchWorkspaces GET /api/workspaces 并返回 slug 列表", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) =>
      jsonResponse({ workspaces: ["story-a", "story-b"] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchWorkspaces()).resolves.toEqual(["story-a", "story-b"]);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/workspaces");
  });

  it("fetchWorkspaces 结构不符 → 报错并给出服务端响应原文", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ nope: true })));
    await expect(fetchWorkspaces()).rejects.toThrow("结构不符");
  });

  it("createWorkspace POST /api/workspaces，body 为 { slug }", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.method).toBe("POST");
      expect(init?.body).toBe(JSON.stringify({ slug: "fresh-story" }));
      return jsonResponse({ slug: "fresh-story" }, 201);
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(createWorkspace("fresh-story")).resolves.toBeUndefined();
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/workspaces");
  });

  it("createWorkspace 失败（422/409）→ detail 文案原样透出", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "slug 不合法：'CON'。…" }, 422)),
    );
    await expect(createWorkspace("CON")).rejects.toThrow("slug 不合法");
  });

  it("fetchBible 请求 /api/{slug}/bible/{type}", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) =>
      jsonResponse({ entity_type: "clue", count: 0, entities: [] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const body = await fetchBible("orient express", "clue");
    expect(body.count).toBe(0);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `/api/${encodeURIComponent("orient express")}/bible/clue`,
    );
  });
});

// ---------- 章节面（P5 T1）：fetch 客户端剧本 ----------

describe("chapter API（P5 T1）", () => {
  afterEach(() => vi.unstubAllGlobals());

  function jsonResponse(payload: unknown, status = 200): Response {
    return new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }

  it("fetchChapters GET /api/{slug}/chapters 并返回 id 列表", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) =>
      jsonResponse({ chapters: ["ch01", "ch02"] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchChapters("story a")).resolves.toEqual(["ch01", "ch02"]);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/story%20a/chapters");
  });

  it("fetchChapters 空工作区 → 空数组（合法空结果，不是错误）", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ chapters: [] })));
    await expect(fetchChapters("empty")).resolves.toEqual([]);
  });

  it("fetchChapter GET /api/{slug}/chapters/{id} 返回正文", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) =>
      jsonResponse({ id: "ch03", content: "# 第3章\n\n正文。" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchChapter("demo", "ch03")).resolves.toBe("# 第3章\n\n正文。");
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/demo/chapters/ch03");
  });

  it("fetchChapter 404 → detail 三要素文案原样透出", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "章节 ch09 不存在。为什么错：…" }, 404)),
    );
    await expect(fetchChapter("demo", "ch09")).rejects.toThrow("章节 ch09 不存在");
  });
});
