/**
 * 会话归并（V05-2B）。
 *
 * 固定事件序列验证三件事：同一条回复持续更新而不是新增气泡；重复、缺批与最终校正
 * 各按约定处理；实时接收与从头回放得到相同结果。
 */

import { describe, expect, it } from "vitest";

import type { WorkspaceEvent } from "../api/workspace";
import {
  appendEvent,
  buildTurns,
  formatDuration,
  NeedsConversationReload,
  orderedBlocks,
  phaseLabel,
  publicText,
  type ChatTurn,
} from "./chatMessages";

let seq = 0;

function next(extra: Record<string, unknown>): WorkspaceEvent {
  seq += 1;
  return { seq, at: "2026-10-21T12:00:00+08:00", ...extra } as WorkspaceEvent;
}

function reset(): void {
  seq = 0;
}

function turnStarted(id = "t1", text = "写第一章"): WorkspaceEvent {
  return next({ type: "turn_started", turn_id: id, text });
}

function started(messageId: string, turnId = "t1"): WorkspaceEvent {
  return next({ type: "assistant_started", turn_id: turnId, message_id: messageId });
}

function blockStarted(
  blockId: string,
  kind: "text" | "thinking" = "text",
  order = 0,
  messageId = "m1",
): WorkspaceEvent {
  return next({
    type: "assistant_block_started",
    turn_id: "t1",
    message_id: messageId,
    block_id: blockId,
    kind,
    order,
  });
}

function delta(blockId: string, part: number, text: string, messageId = "m1"): WorkspaceEvent {
  return next({
    type: "assistant_block_delta",
    turn_id: "t1",
    message_id: messageId,
    block_id: blockId,
    kind: "text",
    part,
    text,
  });
}

function blockFinished(
  blockId: string,
  text: string,
  lastPart: number,
  messageId = "m1",
  completion = "completed",
): WorkspaceEvent {
  return next({
    type: "assistant_block_finished",
    turn_id: "t1",
    message_id: messageId,
    block_id: blockId,
    kind: "text",
    text,
    last_part: lastPart,
    completion,
    duration_ms: 120,
  });
}

function fold(events: WorkspaceEvent[]): ChatTurn[] {
  return events.reduce<ChatTurn[]>((turns, event) => appendEvent(turns, event), []);
}

describe("归并：同一条回复持续更新", () => {
  it("多次增量更新同一条消息，不新增气泡", () => {
    reset();
    const turns = fold([
      turnStarted(),
      started("m1"),
      blockStarted("m1:text:0"),
      delta("m1:text:0", 0, "旅馆的灯"),
      delta("m1:text:0", 1, "熄灭了。"),
    ]);

    expect(turns).toHaveLength(1);
    expect(turns[0]!.items).toHaveLength(1);
    const item = turns[0]!.items[0]!;
    expect(item.kind).toBe("message");
    if (item.kind !== "message") throw new Error("应为消息");
    expect(item.message.blocks).toHaveLength(1);
    expect(item.message.blocks[0]!.text).toBe("旅馆的灯熄灭了。");
    expect(item.message.blocks[0]!.nextPart).toBe(2);
    // 流式期间没有正式公开文本：复制取当前已收到的文字。
    expect(publicText(item.message)).toBe("旅馆的灯熄灭了。");
  });

  it("正式公开文本更新同一条消息，不生成第二个气泡", () => {
    reset();
    const turns = fold([
      turnStarted(),
      started("m1"),
      blockStarted("m1:text:0"),
      delta("m1:text:0", 0, "草稿多半句"),
      blockFinished("m1:text:0", "定稿全文。", 0),
      next({
        type: "assistant_message",
        turn_id: "t1",
        message_id: "m1",
        text: "定稿全文。",
        completion: "completed",
      }),
      next({
        type: "assistant_finished",
        turn_id: "t1",
        message_id: "m1",
        completion: "completed",
        stop_reason: "stop",
        duration_ms: 900,
      }),
    ]);

    expect(turns[0]!.items).toHaveLength(1);
    const item = turns[0]!.items[0]!;
    if (item.kind !== "message") throw new Error("应为消息");
    expect(publicText(item.message)).toBe("定稿全文。");
    expect(item.message.completion).toBe("completed");
    expect(item.message.durationMs).toBe(900);
    // 完整结束值覆盖增量，不拼接两次。
    expect(item.message.blocks[0]!.text).toBe("定稿全文。");
  });

  it("思考与文字按内容位置穿插，思考不进入公开文本", () => {
    reset();
    const turns = fold([
      turnStarted(),
      started("m1"),
      blockStarted("m1:thinking:0", "thinking", 0),
      delta("m1:thinking:0", 0, "先看时间线。"),
      blockStarted("m1:text:1", "text", 1),
      delta("m1:text:1", 0, "时间线是雪夜。"),
    ]);

    const item = turns[0]!.items[0]!;
    if (item.kind !== "message") throw new Error("应为消息");
    expect(orderedBlocks(item.message).map((block) => block.kind)).toEqual(["thinking", "text"]);
    expect(publicText(item.message)).toBe("时间线是雪夜。");
  });

  it("未受影响的轮次保持引用，界面可以跳过重绘", () => {
    reset();
    const first = fold([turnStarted("t1", "第一轮"), started("m1", "t1")]);
    const turns = [first[0]!, ...fold([turnStarted("t2", "第二轮"), started("m2", "t2")])];

    const updated = appendEvent(turns, {
      seq: 900,
      at: "2026-10-21T12:00:01+08:00",
      type: "assistant_block_started",
      turn_id: "t2",
      message_id: "m2",
      block_id: "m2:text:0",
      kind: "text",
      order: 0,
    });

    expect(updated[0]).toBe(turns[0]);
    expect(updated[1]).not.toBe(turns[1]);
  });
});

describe("归并：重复与缺口", () => {
  it("重复的增量与重复的整条事件都不改变结果", () => {
    reset();
    const base = [turnStarted(), started("m1"), blockStarted("m1:text:0"), delta("m1:text:0", 0, "一")];
    const turns = fold(base);
    const again = appendEvent(turns, base[3]!);
    expect(again).toBe(turns);
    // 重复的 part 不重复追加文字。
    const repeated = appendEvent(turns, { ...base[3]!, seq: 99 });
    expect(repeated).toBe(turns);
  });

  it("缺号时要求回读，而不是猜缺失文字", () => {
    reset();
    const turns = fold([turnStarted(), started("m1"), blockStarted("m1:text:0"), delta("m1:text:0", 0, "一")]);
    expect(() => appendEvent(turns, delta("m1:text:0", 2, "三"))).toThrow(NeedsConversationReload);
  });

  it("回读仍有缺口时标记记录不完整，其余内容照常展示", () => {
    reset();
    const turns = buildTurns([
      turnStarted(),
      started("m1"),
      blockStarted("m1:text:0"),
      delta("m1:text:0", 0, "一"),
      delta("m1:text:0", 2, "三"),
      blockFinished("m1:text:0", "一三", 2),
    ]);
    expect(turns[0]!.incomplete).toBe(true);
    const item = turns[0]!.items[0]!;
    if (item.kind !== "message") throw new Error("应为消息");
    expect(publicText(item.message)).toBe("一三");
  });

  it("实时接收与从头回放得到相同展示", () => {
    reset();
    const events = [
      turnStarted(),
      next({ type: "run_phase", turn_id: "t1", phase: "model" }),
      started("m1"),
      blockStarted("m1:text:0"),
      delta("m1:text:0", 0, "先写下"),
      blockFinished("m1:text:0", "先写下这一段。", 0),
      next({ type: "assistant_message", turn_id: "t1", message_id: "m1", text: "先写下这一段。" }),
      next({ type: "assistant_finished", turn_id: "t1", message_id: "m1", completion: "completed" }),
    ];
    const live = fold(events);
    const replay = buildTurns(events);
    expect(replay).toEqual(live);
    expect(replay).toHaveLength(1);
  });
});

describe("归并：工具与结果", () => {
  it("同名工具按调用 ID 分别配对", () => {
    reset();
    const turns = fold([
      turnStarted(),
      next({ type: "tool_started", turn_id: "t1", tool_call_id: "c1", tool: "read_file", args: { path: "正文/1.md" } }),
      next({ type: "tool_started", turn_id: "t1", tool_call_id: "c2", tool: "read_file", args: { path: "设定集/线索.md" } }),
      next({
        type: "tool_finished",
        turn_id: "t1",
        tool_call_id: "c2",
        tool: "read_file",
        ok: true,
        summary: "读到了",
        duration_ms: 30,
      }),
      next({ type: "tool_finished", turn_id: "t1", tool_call_id: "c1", tool: "read_file", ok: false, summary: "没有这个文件" }),
    ]);

    const tools = turns[0]!.items.filter((item) => item.kind === "tool");
    expect(tools).toHaveLength(2);
    if (tools[0]!.kind !== "tool" || tools[1]!.kind !== "tool") throw new Error("应为工具");
    expect(tools[0]!.tool.args.path).toBe("正文/1.md");
    expect(tools[0]!.tool.ok).toBe(false);
    expect(tools[1]!.tool.args.path).toBe("设定集/线索.md");
    expect(tools[1]!.tool.ok).toBe(true);
    expect(tools[1]!.tool.durationMs).toBe(30);
  });

  it("旧记录没有调用 ID 时按同名未完成配对", () => {
    reset();
    const turns = fold([
      turnStarted(),
      next({ type: "tool_started", turn_id: "t1", tool: "read_file", args: { path: "正文/1.md" } }),
      next({ type: "tool_finished", turn_id: "t1", tool: "read_file", args: { path: "正文/1.md" }, ok: true }),
    ]);
    const tools = turns[0]!.items.filter((item) => item.kind === "tool");
    expect(tools).toHaveLength(1);
  });

  it("旧记录的公开回复单独展示", () => {
    reset();
    const turns = fold([
      turnStarted(),
      next({ type: "assistant_message", turn_id: "t1", text: "旧版本的回复。" }),
    ]);
    const item = turns[0]!.items[0]!;
    if (item.kind !== "message") throw new Error("应为消息");
    expect(item.message.id).toBe("legacy:2");
    expect(publicText(item.message)).toBe("旧版本的回复。");
  });

  it("整轮结果与错误各自挂在轮次上", () => {
    reset();
    const turns = fold([
      turnStarted(),
      next({ type: "error", turn_id: "t1", code: "MODEL_CALL_FAILED", message: "502" }),
      next({
        type: "run_finished",
        turn_id: "t1",
        status: "failed",
        recording: "no_change",
        changed_paths: [],
        commit: null,
        note: "",
      }),
    ]);
    expect(turns[0]!.errors[0]!.code).toBe("MODEL_CALL_FAILED");
    expect(turns[0]!.result?.status).toBe("failed");
  });
});

describe("读取辅助", () => {
  it("阶段与耗时的文字不伪造进度", () => {
    expect(phaseLabel("model")).toBe("等待模型响应");
    expect(phaseLabel("tool")).toBe("正在执行工具");
    expect(phaseLabel(null)).toBe("等待模型响应");
    expect(formatDuration(320)).toBe("320 毫秒");
    expect(formatDuration(0)).toBe("<1 毫秒");
    expect(formatDuration(1500)).toBe("1.5 秒");
    expect(formatDuration(null)).toBe("");
  });
});
