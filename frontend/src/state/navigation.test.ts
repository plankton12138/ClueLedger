/** 页面位置解析（0.7）：只接受登记过的作品、枚举内的资料页与整数章号。 */

import { describe, expect, it } from "vitest";

import { pageHash, parseHash } from "./navigation";

describe("parseHash", () => {
  it("解析写作页与资料页", () => {
    expect(parseHash("#/workspaces/ws1/writing")).toEqual({
      kind: "writing",
      workspaceId: "ws1",
    });
    expect(parseHash("#/workspaces/ws1/materials/timeline?chapter=8&entry=abc123")).toEqual({
      kind: "materials",
      workspaceId: "ws1",
      section: "timeline",
      chapter: 8,
      entryId: "abc123",
    });
  });

  it("没有章号时表示全书", () => {
    expect(parseHash("#/workspaces/ws1/materials/clues")).toEqual({
      kind: "materials",
      workspaceId: "ws1",
      section: "clues",
      chapter: null,
    });
  });

  it("拒绝未知资料页、非法章号与非法条目 ID", () => {
    expect(parseHash("#/workspaces/ws1/materials/reports")).toBeNull();
    expect(parseHash("#/workspaces/ws1/materials/timeline?chapter=-1")).toBeNull();
    expect(parseHash("#/workspaces/ws1/materials/timeline?chapter=1.5")).toBeNull();
    expect(parseHash("#/workspaces/ws1/materials/timeline?entry=../x")).toBeNull();
    expect(parseHash("#/workspaces/../etc/passwd")).toBeNull();
    expect(parseHash("#/workspaces/ws1/materials")).toBeNull();
    expect(parseHash("")).toBeNull();
  });
});

describe("pageHash", () => {
  it("往返一致", () => {
    const pages = [
      { kind: "writing" as const, workspaceId: "ws1" },
      {
        kind: "materials" as const,
        workspaceId: "ws1",
        section: "characters" as const,
        chapter: 3,
        entryId: "deadbeef",
      },
      {
        kind: "materials" as const,
        workspaceId: "ws1",
        section: "glossary" as const,
        chapter: null,
      },
    ];
    for (const page of pages) {
      expect(parseHash(pageHash(page))).toEqual(page);
    }
  });
});
