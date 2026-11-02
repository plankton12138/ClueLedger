/** 页面位置解析：写作页保存全书、章节、正文与信息项；旧页面模式只做重定向。 */

import { describe, expect, it } from "vitest";

import { pageHash, parseHash, type AppPage } from "./navigation";

describe("parseHash", () => {
  it("应用级模块库可携带精确版本与作品ID，拒绝路径或无效URI", () => {
    const pages: AppPage[] = [{ kind: "library" }, { kind: "library", moduleId: "mod_1" }, { kind: "library", moduleId: "mod_1", revisionId: "rev_2", workspaceContextId: "ws1" }];
    for (const page of pages) expect(parseHash(pageHash(page))).toEqual(page);
    for (const hash of ["#/library/modules/%ZZ", "#/library/modules/../revisions/x", "#/library?workspace=C:/books", "#/library/modules/mod_1/extra"]) expect(parseHash(hash)).toBeNull();
  });

  it("写作页保存全书、章节与只读正文", () => {
    expect(parseHash("#/workspaces/ws1/writing")).toEqual({ kind: "writing", workspaceId: "ws1" });
    expect(parseHash("#/workspaces/ws1/writing?chapter=ch_1")).toEqual({
      kind: "writing",
      workspaceId: "ws1",
      chapterId: "ch_1",
    });
    expect(parseHash("#/workspaces/ws1/writing?chapter=ch_1&information=move_1")).toEqual({
      kind: "writing",
      workspaceId: "ws1",
      chapterId: "ch_1",
      informationId: "move_1",
    });
    expect(parseHash(`#/workspaces/ws1/writing?prose=${encodeURIComponent("双线/0001 暴雨.md")}`)).toEqual({
      kind: "writing",
      workspaceId: "ws1",
      prosePath: "双线/0001 暴雨.md",
    });
    expect(parseHash("#/workspaces/ws1/writing?chapter=ch_1&prose=0001.md")).toBeNull();
    expect(parseHash("#/workspaces/ws1/writing?information=move_1")).toBeNull();
    expect(parseHash("#/workspaces/ws1/writing?prose=..%2Fsecret.md")).toBeNull();
    expect(parseHash("#/workspaces/ws1/writing?prose=C%3A%5Csecret.md")).toBeNull();
  });

  it("旧构思、线索与本章模式重定向到统一写作页", () => {
    for (const mode of ["story", "clues", "chapter"]) {
      expect(parseHash(`#/workspaces/ws1/writing?mode=${mode}`)).toEqual({ kind: "writing", workspaceId: "ws1" });
    }
    expect(parseHash("#/workspaces/ws1/writing?mode=design")).toBeNull();
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
  it("来源定位携带安全标识与章节数值", () => {
    const sourceId = `ev_${"a".repeat(20)}_${"b".repeat(32)}`;
    const page: AppPage = { kind: "writing", workspaceId: "ws1", chapterId: "ch_1", informationId: "move_1", sourceId, asOfChapter: 6, taskChapter: 4 };
    expect(parseHash(pageHash(page))).toEqual(page);
    for (const query of ["source=../secret", "source=C:/secret", "as_of=-1", "as_of=1.2", "task_chapter=Infinity", "task_chapter=9007199254740992", "chapter=../../x", "information=../x"]) {
      expect(parseHash(`#/workspaces/ws1/writing?${query}`)).toBeNull();
    }
  });

  it("往返一致且不再写出旧模式", () => {
    const pages = [
      { kind: "writing" as const, workspaceId: "ws1" },
      { kind: "writing" as const, workspaceId: "ws1", chapterId: "ch_1" },
      { kind: "writing" as const, workspaceId: "ws1", chapterId: "ch_1", informationId: "move_1" },
      { kind: "writing" as const, workspaceId: "ws1", prosePath: "正文/0001 暴雨.md" },
      { kind: "ideas" as const, workspaceId: "ws1" },
      { kind: "ideas" as const, workspaceId: "ws1", ideaId: "idea_123" },
    ];
    for (const page of pages) {
      expect(parseHash(pageHash(page))).toEqual(page);
    }
    expect(pageHash({ kind: "writing", workspaceId: "ws1" })).not.toContain("mode=");
  });
});
