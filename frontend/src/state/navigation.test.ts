/** 页面位置解析（0.8 E）：章节与时间线合并为同一页，旧路由仍可打开。 */

import { describe, expect, it } from "vitest";

import { pageHash, parseHash } from "./navigation";

describe("parseHash", () => {
  it("解析写作页、灵感页与资料页", () => {
    expect(parseHash("#/workspaces/ws1/writing")).toEqual({
      kind: "writing",
      workspaceId: "ws1",
    });
    expect(parseHash("#/workspaces/ws1/ideas?entry=idea_123")).toEqual({
      kind: "ideas",
      workspaceId: "ws1",
      ideaId: "idea_123",
    });
    expect(parseHash("#/workspaces/ws1/materials/story?order=story&chapter=8&entry=abc123")).toEqual({
      kind: "materials",
      workspaceId: "ws1",
      section: "story",
      chapter: 8,
      order: "story",
      entryId: "abc123",
    });
  });

  it("没有章号时表示全书", () => {
    expect(parseHash("#/workspaces/ws1/materials/clues")).toEqual({
      kind: "materials",
      workspaceId: "ws1",
      section: "clues",
      chapter: null,
      order: "chapter",
    });
  });

  it("旧章节概览与时间线路由落到同一页的不同顺序", () => {
    expect(parseHash("#/workspaces/ws1/materials/chapters?chapter=3")).toMatchObject({
      section: "story",
      chapter: 3,
      order: "chapter",
    });
    expect(parseHash("#/workspaces/ws1/materials/timeline")).toMatchObject({
      section: "story",
      chapter: null,
      order: "story",
    });
  });

  it("拒绝未知资料页、非法章号与非法条目 ID", () => {
    expect(parseHash("#/workspaces/ws1/materials/reports")).toBeNull();
    expect(parseHash("#/workspaces/ws1/materials/story?chapter=-1")).toBeNull();
    expect(parseHash("#/workspaces/ws1/materials/story?chapter=1.5")).toBeNull();
    expect(parseHash("#/workspaces/ws1/materials/story?entry=../x")).toBeNull();
    expect(parseHash("#/workspaces/../etc/passwd")).toBeNull();
    expect(parseHash("#/workspaces/ws1/materials")).toBeNull();
    expect(parseHash("")).toBeNull();
  });
});

describe("pageHash", () => {
  it("往返一致", () => {
    const pages = [
      { kind: "writing" as const, workspaceId: "ws1" },
      { kind: "ideas" as const, workspaceId: "ws1" },
      { kind: "ideas" as const, workspaceId: "ws1", ideaId: "idea_123" },
      {
        kind: "materials" as const,
        workspaceId: "ws1",
        section: "characters" as const,
        chapter: 3,
        order: "chapter" as const,
        entryId: "deadbeef",
      },
      {
        kind: "materials" as const,
        workspaceId: "ws1",
        section: "story" as const,
        chapter: null,
        order: "story" as const,
      },
      {
        kind: "materials" as const,
        workspaceId: "ws1",
        section: "glossary" as const,
        chapter: null,
        order: "chapter" as const,
      },
    ];
    for (const page of pages) {
      expect(parseHash(pageHash(page))).toEqual(page);
    }
  });
});
