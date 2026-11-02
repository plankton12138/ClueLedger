import { afterEach, describe, expect, it, vi } from "vitest";

import { readChapterContext } from "./story";
import { WorkspaceApiError } from "./workspace";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("随章聚合读取", () => {
  it("把章节、方案、游标与上限编进一次请求", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(url);
      return jsonResponse({
        chapter_path: "正文/0001 雨夜.md",
        chapter_digest: "d1",
        variant_id: "main",
        story_revision: "rev_1",
        ledger_revision: "l1",
        view_revision: "v1",
        items: [],
        next_cursor: "",
        truncated: false,
      });
    });

    const view = await readChapterContext("w1", "正文/0001 雨夜.md", {
      variantId: "main",
      cursor: "next",
      limit: 20,
    });

    const url = new URL(calls[0] ?? "", "http://localhost");
    expect(url.pathname).toBe("/api/file-workspaces/w1/story/chapter-context");
    expect(url.searchParams.get("chapter_path")).toBe("正文/0001 雨夜.md");
    expect(url.searchParams.get("variant_id")).toBe("main");
    expect(url.searchParams.get("cursor")).toBe("next");
    expect(url.searchParams.get("limit")).toBe("20");
    expect(view.view_revision).toBe("v1");
  });

  it("游标过期按错误码返回，不伪装成空列表", async () => {
    vi.stubGlobal("fetch", async () =>
      jsonResponse(
        {
          error: {
            code: "CHAPTER_CONTEXT_CURSOR_CONFLICT",
            where: "story.chapter",
            why: "分页游标与当前章节不一致",
            fix: "重新读取本章第一页",
            details: {},
          },
        },
        409,
      ),
    );

    await expect(
      readChapterContext("w1", "正文/0001 雨夜.md", { cursor: "old" }),
    ).rejects.toMatchObject({ code: "CHAPTER_CONTEXT_CURSOR_CONFLICT" });
    await expect(
      readChapterContext("w1", "正文/0001 雨夜.md", { cursor: "old" }),
    ).rejects.toBeInstanceOf(WorkspaceApiError);
  });
});
