/**
 * V03-1.1 主工作区壳状态测试（计划 M01、执行指导 §5.1）。
 *
 * 关键不变量：数据事件与作者导航分离——发送、候选到达、摘要完成、
 * 切图谱返回均不丢位置；任务完成不抢主区；跨作品事件不写入当前状态。
 */

import { describe, expect, it } from "vitest";

import {
  applyOperationResult,
  emptyDeskState,
  navigate,
  readingPositionKey,
  saveReadingPosition,
  selectInitialChapterId,
  type DeskState,
} from "./desk";
import type { OperationEnvelope } from "../api/identities";

const PROJECT = "prj_a";

function envelope(
  operationId: string,
  overrides: Partial<OperationEnvelope> = {},
): OperationEnvelope {
  return {
    operationId,
    status: "committed",
    projectId: PROJECT,
    changedChapterIds: [],
    ...overrides,
  };
}

function stateWithChapter(): DeskState {
  const state = emptyDeskState(PROJECT);
  return navigate(state, { kind: "manuscript", chapterId: "art_ch1" });
}

describe("MainView 主区唯一", () => {
  it("navigate 替换主区；一次只展开一个", () => {
    let state = stateWithChapter();
    expect(state.mainView).toEqual({ kind: "manuscript", chapterId: "art_ch1" });
    state = navigate(state, { kind: "discussion", conversationId: "conv_1" });
    expect(state.mainView).toEqual({ kind: "discussion", conversationId: "conv_1" });
    state = navigate(state, { kind: "detail", detailKind: "diff", id: "d1" });
    expect(state.mainView).toEqual({ kind: "detail", detailKind: "diff", id: "d1" });
  });

  it("候选审阅也在主区展开（V03-9 §4 候选采用链路）", () => {
    let state = stateWithChapter();
    state = navigate(state, { kind: "detail", detailKind: "candidate", id: "msg_cand_1" });
    expect(state.mainView).toEqual({
      kind: "detail",
      detailKind: "candidate",
      id: "msg_cand_1",
    });
    // 采用后回到该章正文：navigate 回 manuscript，候选视图被替换。
    state = navigate(state, { kind: "manuscript", chapterId: "art_ch1" });
    expect(state.mainView).toEqual({ kind: "manuscript", chapterId: "art_ch1" });
  });
});

describe("applyOperationResult：数据事件与作者导航分离", () => {
  it("操作完成不改主区、不改阅读位置", () => {
    const base = saveReadingPosition(stateWithChapter(), {
      projectId: PROJECT,
      chapterId: "art_ch1",
      sourceKind: "formal",
      editSessionId: null,
      scrollTop: 320,
    });
    const next = applyOperationResult(base, envelope("op_1"));
    expect(next.mainView).toEqual(base.mainView);
    expect(next.operations["op_1"]).toBeDefined();
    expect(
      next.readingPositions[
        readingPositionKey({
          projectId: PROJECT,
          chapterId: "art_ch1",
          sourceKind: "formal",
          editSessionId: null,
        })
      ]?.scrollTop,
    ).toBe(320);
  });

  it("跨作品事件整体丢弃（切作品后旧响应不写入新作品状态）", () => {
    const base = stateWithChapter();
    const next = applyOperationResult(base, envelope("op_x", { projectId: "prj_other" }));
    expect(next).toEqual(base);
    expect(next.operations["op_x"]).toBeUndefined();
  });

  it("同一操作幂等重放只保留一份", () => {
    const base = stateWithChapter();
    const once = applyOperationResult(base, envelope("op_1"));
    const twice = applyOperationResult(once, envelope("op_1"));
    expect(Object.keys(twice.operations)).toEqual(["op_1"]);
  });

  it("blocked/failed 结果同样只进数据区", () => {
    const base = stateWithChapter();
    const next = applyOperationResult(
      base,
      envelope("op_2", { status: "blocked", resultUrl: "/x" }),
    );
    expect(next.mainView).toEqual(base.mainView);
    expect(next.operations["op_2"]?.status).toBe("blocked");
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
