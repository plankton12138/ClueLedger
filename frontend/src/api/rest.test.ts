/**
 * refetchTargets 剧本测试（P3 T9，D7）：写工具成功事件 → 精准 refetch 映射。
 * 呈现策略纯函数——「事件进、刷新目标出」，与 reducer 同一测试哲学。
 */

import { describe, expect, it } from "vitest";
import type { EventFrame, ToolExecutionEndEventFrame } from "./types";
import { refetchTargets } from "./rest";

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
