import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import type { BookCluesView, ChapterCluesView, ClueEntryView } from "../../api/writing";
import { BookClues, ChapterClues, ClueEntryList } from "./CluePanel";

function entry(overrides: Partial<ClueEntryView> = {}): ClueEntryView {
  return {
    id: "CL-01",
    kind: "伏笔",
    status: "planted",
    origin: "planned",
    note: "箱扣缺漆",
    anchor_prefix: "箱扣上的漆掉了一块",
    planted_in: 1,
    resolved_in: null,
    ...overrides,
  };
}

const EMPTY: BookCluesView = { entries: [], plans: [], findings: [] };

describe("clue panel", () => {
  it("renders status, chapters and anchor prefix", () => {
    const html = renderToStaticMarkup(<ClueEntryList entries={[entry({ status: "resolved", resolved_in: 5 })]} empty="" />);
    expect(html).toContain("CL-01");
    expect(html).toContain("箱扣缺漆");
    expect(html).toContain("已回收");
    expect(html).toContain("第 1 章埋");
    expect(html).toContain("第 5 章收");
    expect(html).toContain("锚「箱扣上的漆掉了一块…」");
  });

  it("marks migrated entries as waiting for an anchor", () => {
    const html = renderToStaticMarkup(<ClueEntryList entries={[entry({ origin: "migrated", anchor_prefix: null })]} empty="" />);
    expect(html).toContain("未锚定");
    expect(html).toContain("迁移待重锚");
  });

  it("shows the empty hint when a chapter has no clues", () => {
    const html = renderToStaticMarkup(<ClueEntryList entries={[]} empty="这一章还没有登记线索。" />);
    expect(html).toContain("这一章还没有登记线索。");
  });

  it("renders chapter plans and findings", () => {
    const clues: ChapterCluesView = {
      chapter: 1,
      entries: [entry()],
      plans: [{ id: "CL-01", note: "箱扣缺漆", plant_chapter: 1, resolve_chapter: 5 }],
      findings: [{ code: "CLUE_ORPHANED_ANCHOR", message: "第 1 章 CL-01 锚失联" }],
    };
    const html = renderToStaticMarkup(<ChapterClues clues={clues} />);
    expect(html).toContain("本章线索");
    expect(html).toContain("本章计划");
    expect(html).toContain("第 1 章埋、第 5 章收");
    expect(html).toContain("需要注意");
    expect(html).toContain("CLUE_ORPHANED_ANCHOR");
  });

  it("summarises the book and lists unregistered plans", () => {
    const clues: BookCluesView = {
      entries: [entry(), entry({ id: "CL-02", status: "resolved", resolved_in: 5, note: "铜哨" })],
      plans: [
        { id: "CL-01", note: "箱扣缺漆", plant_chapter: 1, resolve_chapter: 5 },
        { id: "CL-09", note: "灰毛线", plant_chapter: 2, resolve_chapter: 4 },
      ],
      findings: [{ code: "CLUE_PLAN_MISSED", message: "计划线索 CL-09 声明本章埋设" }],
    };
    const html = renderToStaticMarkup(<BookClues clues={clues} />);
    expect(html).toContain("共 2 条：未回收 1、已推进 0、已回收 1、不收 0");
    expect(html).toContain("计划未登记");
    const pending = html.slice(html.indexOf("计划未登记"));
    expect(pending).toContain("灰毛线");
    expect(pending).not.toContain("箱扣缺漆");
    expect(html).toContain("CLUE_PLAN_MISSED");
  });

  it("explains an empty registry instead of showing a blank table", () => {
    const html = renderToStaticMarkup(<BookClues clues={EMPTY} />);
    expect(html).toContain("这本书还没有线索登记");
  });
});
