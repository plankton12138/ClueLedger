/**
 * 事件流页纯函数剧本（P6 T5，计划 D5）：分类映射 / 摘要 / 历史-实时合并。
 * 分类规则与后端 events_store.categorize 同口径（后端测试已钉），这里钉
 * 前端消费侧：合并去重的取数窗口语义与 message_update 裁剪是本模块独有。
 */

import { describe, expect, it } from "vitest";
import type { EventFrame, NoticeFrame } from "./types";
import {
  avgToolLatencyMs,
  categorize,
  formatLatencyMs,
  formatTs,
  indexedEvent,
  indexedNotice,
  isKnownEvent,
  mergeEventRows,
  summarize,
  type EventRecordWire,
} from "./events";

/** 历史记录。data 按真实落盘口径传入：agent 事件含 type，notice 不含。 */
function record(seq: number, type: string, data: Record<string, unknown>): EventRecordWire {
  return { ts: "2026-10-09T00:00:00+00:00", seq, type, data };
}

/** 带自定义时间戳的历史行（平均延迟配对用）。 */
function timedRow(
  seq: number,
  type: string,
  iso: string,
  data: Record<string, unknown>,
): EventRecordWire {
  return { ts: iso, seq, type, data };
}

describe("categorize", () => {
  it("与后端同口径：isError 强制归错误，前缀分桶，其余 other", () => {
    expect(categorize("message_end", {})).toBe("message");
    expect(categorize("tool_execution_start", {})).toBe("tool");
    expect(categorize("tool_execution_end", { isError: true })).toBe("error");
    expect(categorize("tool_execution_end", { isError: false })).toBe("tool");
    expect(categorize("agent_start", {})).toBe("other");
    expect(categorize("notice", { kind: "sync_end" })).toBe("other");
  });
});

describe("isKnownEvent", () => {
  it("tau 契约内 + notice 已知；其余红显", () => {
    expect(isKnownEvent("agent_end")).toBe(true);
    expect(isKnownEvent("tool_execution_end")).toBe(true);
    expect(isKnownEvent("notice")).toBe(true);
    expect(isKnownEvent("mystery_event")).toBe(false);
  });
});

describe("mergeEventRows", () => {
  it("历史记录铺底（seq 作 key），时间/游标保留", () => {
    const rows = mergeEventRows(
      [record(1, "agent_start", { type: "agent_start" }), record(2, "agent_end", { type: "agent_end" })],
      [],
      [],
    );
    expect(rows.map((row) => row.key)).toEqual(["s1", "s2"]);
    expect(rows[0]?.ts).toBe("2026-10-09T00:00:00+00:00");
    expect(rows[0]?.seq).toBe(1);
  });

  it("取数窗口内的帧与历史重叠 → 按指纹去重；新帧保留", () => {
    const records = [
      record(1, "agent_start", { type: "agent_start" }),
      record(2, "message_end", { type: "message_end", message: { role: "user" } }),
    ];
    const inflight = [
      indexedEvent(0, { type: "agent_start" } as EventFrame), // 与 s1 同帧：去重
      indexedEvent(1, { type: "turn_start" } as EventFrame), // 历史没有：保留
    ];
    const rows = mergeEventRows(records, inflight, []);
    expect(rows.map((row) => row.key)).toEqual(["s1", "s2", "e1"]);
    expect(rows[2]?.type).toBe("turn_start");
  });

  it("窗口后的帧无条件追加（必然晚于服务端读盘）", () => {
    const records = [record(1, "agent_start", { type: "agent_start" })];
    const live = [
      indexedEvent(5, { type: "agent_start" } as EventFrame), // 与历史同指纹也不去重
      indexedEvent(6, { type: "agent_end" } as EventFrame),
    ];
    const rows = mergeEventRows(records, [], live);
    expect(rows.map((row) => row.key)).toEqual(["s1", "e5", "e6"]);
    expect(rows[1]?.ts).toBeNull();
  });

  it("message_update 流式中间态不入合并结果（与 events.jsonl 同口径）", () => {
    const frame = {
      type: "message_update",
      message: {},
      assistantMessageEvent: { type: "text_delta", delta: "雪" },
    } as unknown as EventFrame;
    const rows = mergeEventRows([], [indexedEvent(0, frame)], [indexedEvent(1, frame)]);
    expect(rows).toEqual([]);
  });

  it("notice 帧按账本口径去重（落盘 data 不含 type）", () => {
    const notice: NoticeFrame = {
      type: "notice",
      kind: "sync_end",
      chapter: "ch01",
      hunks: 2,
      updates: 5,
      status: "done",
    };
    const records = [record(1, "notice", { kind: "sync_end", chapter: "ch01", hunks: 2, updates: 5, status: "done" })];
    const rows = mergeEventRows(records, [indexedNotice(0, notice)], []);
    expect(rows.map((row) => row.key)).toEqual(["s1"]); // 实时 notice 与历史同帧：去重
  });

  it("实时行 key 用绝对序号，跨重渲染稳定", () => {
    const live = [indexedEvent(7, { type: "turn_start" } as EventFrame)];
    expect(mergeEventRows([], [], live)[0]?.key).toBe("e7");
  });
});

describe("summarize", () => {
  it("运行/回合/对账一句话", () => {
    expect(summarize("agent_start", {})).toBe("运行开始");
    expect(summarize("agent_end", { messages: [{}, {}, {}] })).toBe("运行结束 · 3 条消息");
    expect(summarize("turn_start", {})).toBe("回合开始");
    expect(summarize("notice", { kind: "sync_end", chapter: "ch03", updates: 4 })).toBe(
      "对账 sync_end ch03 · 更新 4 处",
    );
  });

  it("工具帧带结果标记；消息帧摘正文头", () => {
    expect(summarize("tool_execution_start", { toolName: "bible_upsert" })).toBe("bible_upsert");
    expect(summarize("tool_execution_end", { toolName: "bible_upsert", isError: true })).toBe(
      "bible_upsert ✗ 失败",
    );
    expect(summarize("tool_execution_end", { toolName: "bible_query", isError: false })).toBe(
      "bible_query ✓",
    );
    expect(
      summarize("message_end", { message: { role: "assistant", content: [{ type: "text", text: "线索已登记。" }] } }),
    ).toBe("定稿 · assistant · 线索已登记。");
  });

  it("契约外类型原样展示（显式上报，不静默翻译）", () => {
    expect(summarize("mystery_event", {})).toBe("mystery_event");
  });
});

describe("formatTs", () => {
  it("UTC ISO → 本地时分秒；非法输入原样返回", () => {
    const formatted = formatTs("2026-10-09T00:00:00+00:00");
    expect(formatted).toMatch(/^\d{2}:\d{2}:\d{2}$/);
    expect(formatTs("not-a-date")).toBe("not-a-date");
  });
});

describe("avgToolLatencyMs（P7 T4 计划 D8）", () => {
  const start = (seq: number, id: string, iso: string): EventRecordWire =>
    timedRow(seq, "tool_execution_start", iso, { type: "tool_execution_start", toolCallId: id });
  const end = (seq: number, id: string, iso: string, isError = false): EventRecordWire =>
    timedRow(seq, "tool_execution_end", iso, {
      type: "tool_execution_end",
      toolCallId: id,
      isError,
    });

  it("同 toolCallId 的 start→end 帧对取时长均值", () => {
    const rows = mergeEventRows(
      [
        start(1, "c1", "2026-10-09T00:00:00+00:00"),
        end(2, "c1", "2026-10-09T00:00:01+00:00"), // 1000 ms
        start(3, "c2", "2026-10-09T00:00:00+00:00"),
        end(4, "c2", "2026-10-09T00:00:03+00:00"), // 3000 ms
      ],
      [],
      [],
    );
    expect(avgToolLatencyMs(rows)).toBe(2000);
  });

  it("实时行（ts=null）与无帧对时不参与：返回 null（UI 显示「—」）", () => {
    const live = [indexedEvent(0, { type: "tool_execution_start", toolCallId: "c1" } as EventFrame)];
    expect(avgToolLatencyMs(mergeEventRows([], [], live))).toBeNull();
    expect(avgToolLatencyMs([])).toBeNull();
    // 只有 start 没有 end：无帧对
    const onlyStart = mergeEventRows([start(1, "c1", "2026-10-09T00:00:00+00:00")], [], []);
    expect(avgToolLatencyMs(onlyStart)).toBeNull();
  });

  it("toolCallId 缺失或时间戳非法的行跳过，不影响其余配对", () => {
    const rows = mergeEventRows(
      [
        timedRow(1, "tool_execution_start", "2026-10-09T00:00:00+00:00", { type: "tool_execution_start" }),
        end(2, "c1", "2026-10-09T00:00:02+00:00"), // 无 start：跳过
        start(3, "c2", "2026-10-09T00:00:00+00:00"),
        timedRow(4, "tool_execution_end", "bad-ts", { type: "tool_execution_end", toolCallId: "c2" }), // 非法 ts：跳过
        start(5, "c3", "2026-10-09T00:00:00+00:00"),
        end(6, "c3", "2026-10-09T00:00:01.500+00:00"), // 1500 ms
      ],
      [],
      [],
    );
    expect(avgToolLatencyMs(rows)).toBe(1500);
  });

  it("isError 的 end 照常配对；end 早于 start 的畸形对不计入", () => {
    const rows = mergeEventRows(
      [
        start(1, "c1", "2026-10-09T00:00:00+00:00"),
        end(2, "c1", "2026-10-09T00:00:00.800+00:00", true), // 800 ms（失败工具也计延迟）
        start(3, "c2", "2026-10-09T00:00:05+00:00"),
        end(4, "c2", "2026-10-09T00:00:04+00:00"), // end < start：丢弃
      ],
      [],
      [],
    );
    expect(avgToolLatencyMs(rows)).toBe(800);
  });
});

describe("formatLatencyMs", () => {
  it("≥1 s 显示秒（一位小数），否则毫秒取整", () => {
    expect(formatLatencyMs(320)).toBe("320 ms");
    expect(formatLatencyMs(1500)).toBe("1.5 s");
    expect(formatLatencyMs(1234.6)).toBe("1.2 s");
  });
});
