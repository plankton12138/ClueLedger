/**
 * 事件 → UI 状态的纯函数 reducer（P3 T7，计划 D8）——adapter.py 的 TS 等价物，
 * 去掉 session 层分支（compaction/retry/queue 不在 hastings 契约内）。
 *
 * 设计要点：
 * - 纯函数：applyEvent/applyMessages 不改动传入状态（「事件序列进、状态断言出」
 *   就是 FakeProvider 哲学在前端的对应物）；
 * - 定稿以 canonical 消息为准：流式 delta 只养 buffer，message_end 用消息本体
 *   生成条目（adapter.py 的"块边界与顺序以持久化为准"原则）；
 * - 未知事件类型显式进 unhandledEventTypes，不静默（PlainRenderer 惯例）；
 * - eventLog 保留全部原始帧，调试视图（T9）与 unhandled 高亮共用。
 */

import type {
  AgentMessageWire,
  AssistantMessageWire,
  EventFrame,
  UserMessageWire,
} from "../api/types";
import {
  assistantMessageText,
  assistantThinkingText,
  toolResultMessageText,
  toolResultPayloadText,
  userMessageText,
} from "../api/types";

export type ToolCardStatus = "running" | "ok" | "error";

export type ChatItem =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "thinking"; text: string }
  | {
      kind: "tool";
      toolCallId: string;
      toolName: string;
      args: Record<string, unknown>;
      status: ToolCardStatus;
      resultText: string;
      resultDetails: unknown;
    }
  | { kind: "error"; text: string };

export interface UiState {
  running: boolean;
  items: ChatItem[];
  /** 流式中的助手正文与思考文本（message_end 定稿后清空）。 */
  assistantBuffer: string;
  thinkingBuffer: string;
  /** 契约外事件类型（去重上报）；快照重放时也收纳契约外 role（message:xxx）。 */
  unhandledEventTypes: string[];
  /** 全部原始事件帧（调试视图数据源）。 */
  eventLog: EventFrame[];
}

export const initialUiState: UiState = {
  running: false,
  items: [],
  assistantBuffer: "",
  thinkingBuffer: "",
  unhandledEventTypes: [],
  eventLog: [],
};

function itemsFromAssistant(m: AssistantMessageWire): ChatItem[] {
  const out: ChatItem[] = [];
  const thinking = assistantThinkingText(m);
  if (thinking) out.push({ kind: "thinking", text: thinking });
  const text = assistantMessageText(m);
  if (text) out.push({ kind: "assistant", text });
  return out;
}

/** 流被中断（未走到 message_end）时，把 buffer 里已有的文本落成条目。 */
function flushBuffers(s: UiState): UiState {
  if (!s.assistantBuffer && !s.thinkingBuffer) return s;
  const items = [...s.items];
  if (s.thinkingBuffer) items.push({ kind: "thinking", text: s.thinkingBuffer });
  if (s.assistantBuffer) items.push({ kind: "assistant", text: s.assistantBuffer });
  return { ...s, items, assistantBuffer: "", thinkingBuffer: "" };
}

/** 契约外事件上报（去重）；reducer 与 store（notice 帧）共用同一口子。 */
export function reportUnhandled(s: UiState, type: string): UiState {
  return {
    ...s,
    unhandledEventTypes: s.unhandledEventTypes.includes(type)
      ? s.unhandledEventTypes
      : [...s.unhandledEventTypes, type],
  };
}

function userItem(m: UserMessageWire): ChatItem {
  return { kind: "user", text: userMessageText(m) };
}

export function applyEvent(state: UiState, event: EventFrame): UiState {
  const base: UiState = { ...state, eventLog: [...state.eventLog, event] };
  switch (event.type) {
    case "agent_start":
      return { ...base, running: true };
    case "agent_end":
      return flushBuffers({ ...base, running: false });
    case "message_start":
      // 流式文本从 message_update 的 delta 开始累积；user 消息在 message_end 落条目
      return base;
    case "message_update": {
      const nested = event.assistantMessageEvent;
      switch (nested.type) {
        case "text_delta":
          return { ...base, assistantBuffer: base.assistantBuffer + nested.delta };
        case "thinking_delta":
          return { ...base, thinkingBuffer: base.thinkingBuffer + nested.delta };
        default:
          // start/end/toolcall_*/done/error：已知嵌套事件，流 UI 不消费
          // （toolcall 的落点是工具执行事件；partial 全量消息太重，不进 buffer）
          return base;
      }
    }
    case "message_end": {
      const m = event.message;
      if (m.role === "assistant") {
        if (m.stopReason === "error" || m.stopReason === "aborted") {
          const text = m.errorMessage || assistantMessageText(m) || "（无错误详情）";
          return {
            ...base,
            items: [...base.items, { kind: "error", text }],
            assistantBuffer: "",
            thinkingBuffer: "",
          };
        }
        return {
          ...base,
          items: [...base.items, ...itemsFromAssistant(m)],
          assistantBuffer: "",
          thinkingBuffer: "",
        };
      }
      if (m.role === "user") {
        return { ...base, items: [...base.items, userItem(m)] };
      }
      return base; // toolResult 等角色的 message_end：落点在工具执行事件
    }
    case "tool_execution_start": {
      const flushed = flushBuffers(base);
      return {
        ...flushed,
        items: [
          ...flushed.items,
          {
            kind: "tool",
            toolCallId: event.toolCallId,
            toolName: event.toolName,
            args: event.args,
            status: "running",
            resultText: "",
            resultDetails: null,
          },
        ],
      };
    }
    case "tool_execution_update":
      return {
        ...base,
        items: base.items.map((it) =>
          it.kind === "tool" && it.toolCallId === event.toolCallId
            ? { ...it, resultText: toolResultPayloadText(event.partialResult) }
            : it,
        ),
      };
    case "tool_execution_end":
      return {
        ...base,
        items: base.items.map((it) =>
          it.kind === "tool" && it.toolCallId === event.toolCallId
            ? {
                ...it,
                status: event.isError ? ("error" as const) : ("ok" as const),
                resultText: toolResultPayloadText(event.result),
                resultDetails: event.result.details ?? null,
              }
            : it,
        ),
      };
    case "turn_start":
    case "turn_end":
      return base; // 已知事件，UI 无效果
    default:
      return reportUnhandled(base, (event as { type: string }).type);
  }
}

/** get_messages 快照重放：浏览器刷新后用服务端历史还原对话流（计划 D4）。 */
export function applyMessages(state: UiState, messages: AgentMessageWire[]): UiState {
  let s = state;
  for (const m of messages) {
    switch (m.role) {
      case "user":
        s = { ...s, items: [...s.items, userItem(m)] };
        break;
      case "assistant":
        s = { ...s, items: [...s.items, ...itemsFromAssistant(m)] };
        break;
      case "toolResult":
        s = {
          ...s,
          items: [
            ...s.items,
            {
              kind: "tool",
              toolCallId: m.toolCallId,
              toolName: m.toolName,
              args: {}, // 快照里没有调用参数（ToolResultMessage 不含 args）
              status: m.isError ? ("error" as const) : ("ok" as const),
              resultText: toolResultMessageText(m),
              resultDetails: m.details ?? null,
            },
          ],
        };
        break;
      default:
        s = reportUnhandled(s, `message:${m.role}`);
        break;
    }
  }
  return s;
}
