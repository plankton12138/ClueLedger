/**
 * V03-1.1 主工作区壳状态测试（计划 M01、执行指导 §5.1）。
 *
 * 关键不变量：主区一次只展开一个；阅读位置四元组按章保存、跨项目隔离。
 * V03-10 M7：操作结果缓存随后续状态 / 操作结果面板撤下移除，相关用例删除。
 */

import { describe, expect, it } from "vitest";

import {
  emptyDeskState,
  navigate,
  readingPositionKey,
  saveReadingPosition,
  selectInitialChapterId,
  type DeskState,
} from "./desk";

const PROJECT = "prj_a";

function stateWithChapter(): DeskState {
  const state = emptyDeskState(PROJECT);
  return navigate(state, { kind: "manuscript", chapterId: "art_ch1" });
}

describe("MainView 主区唯一", () => {
  it("navigate 替换主区；一次只展开一个", () => {
    let state = stateWithChapter();
    expect(state.mainView).toEqual({ kind: "manuscript", chapterId: "art_ch1" });
    state = navigate(state, { kind: "manuscript", chapterId: "art_ch2" });
    expect(state.mainView).toEqual({ kind: "manuscript", chapterId: "art_ch2" });
    // V03-10 M6：无正文时主区可为空（按构思状态处理）。
    state = navigate(state, null);
    expect(state.mainView).toBeNull();
  });
});

describe("阅读位置四元组键", () => {
  it("source_kind / edit_session_id 不同即不同键（I01 身份不同）", () => {
    const formal = {
      projectId: PROJECT,
      chapterId: "art_ch1",
      sourceKind: "formal" as const,
      editSessionId: null,
    };
    const draft = { ...formal, sourceKind: "edit_draft" as const, editSessionId: "eds_1" };
    expect(readingPositionKey(formal)).not.toBe(readingPositionKey(draft));
    expect(
      readingPositionKey({ ...draft, editSessionId: "eds_2" }),
    ).not.toBe(readingPositionKey(draft));
  });

  it("saveReadingPosition 不改主区", () => {
    const base = stateWithChapter();
    const next = saveReadingPosition(base, {
      projectId: PROJECT,
      chapterId: "art_ch2",
      sourceKind: "formal",
      editSessionId: null,
    });
    expect(next.mainView).toEqual(base.mainView);
    expect(next.readingPositions[readingPositionKey({
      projectId: PROJECT,
      chapterId: "art_ch2",
      sourceKind: "formal",
      editSessionId: null,
    })]).toBeDefined();
  });
});

describe("壳层初始焦点（存在正式正文）", () => {
  it("有正文：恢复上次章节", () => {
    let state = emptyDeskState(PROJECT);
    state = saveReadingPosition(state, {
      projectId: PROJECT,
      chapterId: "art_ch3",
      sourceKind: "formal",
      editSessionId: null,
    });
    expect(
      selectInitialChapterId(["art_ch1", "art_ch2", "art_ch3"], state, PROJECT),
    ).toBe("art_ch3");
  });

  it("无记录：取目录第一章", () => {
    expect(selectInitialChapterId(["art_ch1", "art_ch2"], emptyDeskState(PROJECT), PROJECT)).toBe(
      "art_ch1",
    );
  });

  it("无正文（只有未采用候选）：构思状态，无章节焦点", () => {
    expect(selectInitialChapterId([], emptyDeskState(PROJECT), PROJECT)).toBeNull();
  });

  it("保存的章节已不存在（被删除）：回落第一章", () => {
    let state = emptyDeskState(PROJECT);
    state = saveReadingPosition(state, {
      projectId: PROJECT,
      chapterId: "art_gone",
      sourceKind: "formal",
      editSessionId: null,
    });
    expect(selectInitialChapterId(["art_ch1"], state, PROJECT)).toBe("art_ch1");
  });

  it("阅读位置按项目隔离", () => {
    let state = emptyDeskState(PROJECT);
    state = saveReadingPosition(state, {
      projectId: "prj_other",
      chapterId: "art_other",
      sourceKind: "formal",
      editSessionId: null,
    });
    expect(selectInitialChapterId(["art_ch1"], state, PROJECT)).toBe("art_ch1");
  });
});
