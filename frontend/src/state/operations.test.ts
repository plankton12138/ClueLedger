/**
 * 操作事件归并：代次隔离、游标去重与暂停后的迟到输出。
 */

import { describe, expect, it } from "vitest";

import {
  currentBlocks,
  emptyOperationStream,
  reduceOperationEvent,
  type OperationEvent,
  type OperationStreamState,
} from "./operations";

function attempt(seq: number, generation: number): OperationEvent {
  return { seq, operation_id: "op_1", generation, type: "attempt_started" };
}

function delta(seq: number, generation: number, block: string, text: string): OperationEvent {
  return {
    seq,
    operation_id: "op_1",
    generation,
    type: "assistant_block_delta",
    block_id: block,
    text,
  };
}

function reduceAll(events: OperationEvent[]): OperationStreamState {
  return events.reduce(reduceOperationEvent, emptyOperationStream("op_1"));
}

describe("操作事件归并", () => {
  it("同一代次的增量按块拼接", () => {
    const state = reduceAll([attempt(1, 1), delta(2, 1, "b1", "第一段"), delta(3, 1, "b1", "续写")]);

    expect(currentBlocks(state)).toEqual({ b1: "第一段续写" });
    expect(state.cursor).toBe(3);
    expect(state.paused).toBe(false);
  });

  it("其他操作与已经处理过的序号不参与归并", () => {
    const state = reduceAll([attempt(1, 1), delta(2, 1, "b1", "正常")]);
    const foreign = reduceOperationEvent(state, {
      seq: 3,
      operation_id: "op_other",
      generation: 1,
      type: "assistant_block_delta",
      block_id: "b1",
      text: "别的任务",
    });
    const replay = reduceOperationEvent(foreign, delta(2, 1, "b1", "重复"));

    expect(foreign).toBe(state);
    expect(replay.blocks).toEqual({ "1:b1": "正常" });
  });

  it("暂停之后到达的增量不再进入显示", () => {
    const state = reduceAll([
      attempt(1, 1),
      delta(2, 1, "b1", "写到一半"),
      { seq: 3, operation_id: "op_1", generation: 1, type: "stop_requested" },
      delta(4, 1, "b1", "晚到的内容"),
    ]);

    expect(state.paused).toBe(true);
    expect(currentBlocks(state)).toEqual({ b1: "写到一半" });
    expect(state.cursor).toBe(4);
  });

  it("新代次保留旧代内容并清掉暂停标记", () => {
    const state = reduceAll([
      attempt(1, 1),
      delta(2, 1, "b1", "第一代"),
      { seq: 3, operation_id: "op_1", generation: 1, type: "stop_requested" },
      attempt(4, 2),
      delta(5, 2, "b1", "第二代"),
    ]);

    expect(state.generation).toBe(2);
    expect(state.paused).toBe(false);
    // 旧代次的内容还在，但当前显示只取新代次。
    expect(state.blocks).toEqual({ "1:b1": "第一代", "2:b1": "第二代" });
    expect(currentBlocks(state)).toEqual({ b1: "第二代" });
  });

  it("旧代次迟到的事件只推进游标", () => {
    const state = reduceAll([attempt(1, 1), attempt(3, 2), delta(2, 1, "b1", "旧代")]);

    expect(state.generation).toBe(2);
    expect(state.blocks).toEqual({});
    expect(state.cursor).toBe(3);
  });

  it("没有先看到新代次开始时要求先取快照", () => {
    const state = reduceAll([attempt(1, 1), delta(2, 1, "b1", "旧代"), delta(3, 2, "b1", "新代")]);

    expect(state.needsSnapshot).toBe(true);
    expect(state.generation).toBe(1);
    expect(currentBlocks(state)).toEqual({ b1: "旧代" });
  });
});
