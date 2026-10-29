/** 页面位置解析：只保留写作与灵感两页；故事资料页已移除。 */

import { describe, expect, it } from "vitest";

import { pageHash, parseHash, type AppPage } from "./navigation";

describe("parseHash", () => {
  it("应用级模块库可携带精确版本与作品ID，拒绝路径或无效URI", () => {
    const pages: AppPage[] = [{ kind: "library" }, { kind: "library", moduleId: "mod_1" }, { kind: "library", moduleId: "mod_1", revisionId: "rev_2", workspaceContextId: "ws1" }];
    for (const page of pages) expect(parseHash(pageHash(page))).toEqual(page);
    for (const hash of ["#/library/modules/%ZZ", "#/library/modules/../revisions/x", "#/library?workspace=C:/books", "#/library/modules/mod_1/extra"]) expect(parseHash(hash)).toBeNull();
  });
  it("解析写作页与灵感页", () => {
    expect(parseHash("#/workspaces/ws1/writing")).toEqual({
      kind: "writing",
      workspaceId: "ws1",
    });
    expect(parseHash("#/workspaces/ws1/ideas?entry=idea_123")).toEqual({
      kind: "ideas",
      workspaceId: "ws1",
      ideaId: "idea_123",
    });
  });

  it("旧资料页与总览页回落到写作页，不保留兼容页", () => {
    for (const hash of [
      "#/workspaces/ws1/materials/story?order=story&chapter=8&entry=abc123",
      "#/workspaces/ws1/materials/clues",
      "#/workspaces/ws1/materials/chapters?chapter=3",
      "#/workspaces/ws1/materials/timeline",
      "#/workspaces/ws1/materials/reports",
      "#/workspaces/ws1/materials",
    ]) {
      expect(parseHash(hash)).toEqual({ kind: "writing", workspaceId: "ws1" });
    }
    expect(parseHash("#/workspaces/book-a/overview?order=story&event=event_1")).toEqual({
      kind: "writing",
      workspaceId: "book-a",
    });
  });

  it("仍然拒绝路径穿越与畸形片段", () => {
    expect(parseHash("#/workspaces/../etc/passwd")).toBeNull();
    expect(parseHash("#/workspaces/book-a/overview/extra")).toBeNull();
    expect(parseHash("")).toBeNull();
  });
});

describe("pageHash", () => {
  it("source routes carry safe IDs and independent chapter values", () => {
    const sourceId = `ev_${"a".repeat(20)}_${"b".repeat(32)}`;
    const page: AppPage = { kind: "writing", workspaceId: "ws1", sourceId, clueId: "clue_one", asOfChapter: 6, taskChapter: 4 };
    expect(parseHash(pageHash(page))).toEqual(page);
    for (const query of ["source=../secret", "source=C:/secret", "as_of=-1", "as_of=1.2", "task_chapter=Infinity", "task_chapter=9007199254740992", "clue=../../x"]) {
      expect(parseHash(`#/workspaces/ws1/writing?${query}`)).toBeNull();
    }
  });
  it("往返一致", () => {
    const pages = [
      { kind: "writing" as const, workspaceId: "ws1" },
      { kind: "ideas" as const, workspaceId: "ws1" },
      { kind: "ideas" as const, workspaceId: "ws1", ideaId: "idea_123" },
    ];
    for (const page of pages) {
      expect(parseHash(pageHash(page))).toEqual(page);
    }
  });
});
