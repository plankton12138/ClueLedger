/**
 * 事件流页纯函数（P6 T5，计划 D5）：分类映射 / 摘要 / 历史-实时合并。
 *
 * 分类规则与后端 hastings/events_store.py 的 categorize 同一套口径
 * （isError 强制归错误 → message_* → tool_execution_* → other）——REST
 * 服务端过滤与前端实时帧过滤必须一致，规则演进双侧同步。
 *
 * 合并策略（无 seq 的实时帧 vs 带 seq 的历史账本）：
 * - 历史记录来自 REST（events.jsonl，有 seq/ts），按 seq 升序铺底；
 * - 实时帧来自 WS（无 seq/ts）。取数窗口内（fetch 在途）到达的帧可能与
 *   历史重叠，按「账本指纹」去重；窗口后到达的帧必然晚于服务端读盘，
 *   无条件追加。指纹 = JSON(帧原文)，碰撞需同型同载荷帧挤进同一毫秒级
 *   窗口——同型帧之间隔着秒级模型往返，实际不可能。
 * - message_update 刻意不入合并结果：它是流式中间态，events.jsonl 不收
 *   （T4 决策），事件流页是账本的实时镜像，口径必须一致。
 */

import type { EventFrame, NoticeFrame } from "./types";
import { AGENT_EVENT_TYPES } from "./types";
import type { EventRecordWire } from "./rest";

export type { EventRecordWire };

export type EventCategory = "message" | "tool" | "error" | "other";

/** 与后端 categorize 同规则；isError 帧强制归错误（压过类型前缀）。 */
export function categorize(type: string, frame: Record<string, unknown>): EventCategory {
  if (frame["isError"] === true) return "error";
  if (type.startsWith("message")) return "message";
  if (type.startsWith("tool_execution")) return "tool";
  return "other";
}

/** 事件流页一行：历史行有 ts/seq，实时行标注 null（时间列显示「实时」）。 */
export interface EventRow {
  key: string;
  ts: string | null;
  seq: number | null;
  type: string;
  frame: Record<string, unknown>;
}

/** 带绝对序号的实时帧——view 层从 store 切片时携带，保证行 key 稳定。 */
export interface IndexedFrame {
  index: number;
  type: string;
  /** 账本口径的帧原文：agent 事件含 type，notice 不含（与落盘同形）。 */
  frame: Record<string, unknown>;
}

/** WS 帧原样（含 type）→ 账本口径。 */
export function indexedEvent(index: number, frame: EventFrame): IndexedFrame {
  return { index, type: frame.type, frame: frame as unknown as Record<string, unknown> };
}

/** WS notice 帧 → 账本口径：落盘时 type 单列、data 不含 type（_persist_notice）。 */
export function indexedNotice(index: number, notice: NoticeFrame): IndexedFrame {
  const { type: _type, ...payload } = notice;
  return { index, type: "notice", frame: payload as Record<string, unknown> };
}

/** 取数窗口内到达的帧按账本指纹去重；窗口后的帧无条件追加。 */
export function mergeEventRows(
  records: EventRecordWire[],
  inflight: IndexedFrame[],
  live: IndexedFrame[],
): EventRow[] {
  const rows: EventRow[] = records.map((record) => ({
    key: `s${record.seq}`,
    ts: record.ts,
    seq: record.seq,
    type: record.type,
    frame: record.data,
  }));
  const seen = new Set(records.map((record) => JSON.stringify(record.data)));
  for (const { index, type, frame } of inflight) {
    if (type === "message_update") continue; // 流式中间态不入账（与 events.jsonl 同口径）
    if (seen.has(JSON.stringify(frame))) continue;
    rows.push({ key: `e${index}`, ts: null, seq: null, type, frame });
  }
  for (const { index, type, frame } of live) {
    if (type === "message_update") continue;
    rows.push({ key: `e${index}`, ts: null, seq: null, type, frame });
  }
  return rows;
}

/** 事件行是否属于契约外类型（红色高亮）：notice 是 hastings 契约内加法扩展。 */
export function isKnownEvent(type: string): boolean {
  return type === "notice" || (AGENT_EVENT_TYPES as readonly string[]).includes(type);
}

/** 行摘要（单行截断，细节靠点击展开 JSON）：类型 → 人话一句话。 */
export function summarize(type: string, frame: Record<string, unknown>): string {
  switch (type) {
    case "agent_start":
      return "运行开始";
    case "agent_end": {
      const messages = frame["messages"];
      const count = Array.isArray(messages) ? messages.length : 0;
      return `运行结束 · ${count} 条消息`;
    }
    case "turn_start":
      return "回合开始";
    case "turn_end":
      return "回合结束";
    case "notice": {
      const kind = String(frame["kind"] ?? "?");
      const chapter = String(frame["chapter"] ?? "");
      const updates = frame["updates"];
      const suffix = typeof updates === "number" ? ` · 更新 ${updates} 处` : "";
      return `对账 ${kind} ${chapter}${suffix}`;
    }
    default:
      break;
  }
  if (type.startsWith("tool_execution")) {
    const name = typeof frame["toolName"] === "string" ? frame["toolName"] : "?";
    if (type === "tool_execution_end") {
      return `${name} ${frame["isError"] === true ? "✗ 失败" : "✓"}`;
    }
    return String(name);
  }
  if (type === "message_start" || type === "message_end") {
    const message = frame["message"] as { role?: string; content?: unknown } | undefined;
    const role = typeof message?.role === "string" ? message.role : "?";
    return `${type === "message_end" ? "定稿" : "开始"} · ${role} · ${messageTextHead(message)}`;
  }
  if (type === "message_update") {
    const nested = frame["assistantMessageEvent"] as { type?: string } | undefined;
    return `流式 · ${typeof nested?.type === "string" ? nested.type : "?"}`;
  }
  return type; // 契约外类型：原样展示（显式上报，不静默翻译）
}

function messageTextHead(message: { role?: string; content?: unknown } | undefined): string {
  if (message === undefined || message.content === undefined) return "";
  const blocks = typeof message.content === "string" ? [{ type: "text", text: message.content }] : (message.content as { type: string; text?: string }[]);
  const text = blocks
    .filter((block) => block.type === "text" && typeof block.text === "string")
    .map((block) => block.text as string)
    .join("");
  return text.length > 60 ? `${text.slice(0, 60)}…` : text;
}

/** UTC ISO → 本地 HH:MM:SS（历史行时间列）。 */
export function formatTs(ts: string): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return ts;
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}
