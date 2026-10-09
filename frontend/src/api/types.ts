/**
 * WS 帧协议类型——与 tau_agent 事件契约一一对应（P3 T7）。
 *
 * 对应关系（Python → wire → TS）：
 * - tau_agent/events.py 的 AgentEvent 判别联合 → 本文件 AgentEventFrame；
 * - tau_agent/provider_events.py 的 AssistantMessageEvent → AssistantMessageEventFrame；
 * - tau_agent/messages.py 的 AgentMessage → AgentMessageWire。
 *
 * 字段名全部按 wire 别名（WireModel 的 camelCase alias_generator + 显式
 * serialization_alias）：tool_call_id→toolCallId、is_error→isError、
 * assistant_message_event→assistantMessageEvent。Python 侧测试
 * （test_server_ws.py）已钉住这些别名在 JSON 层保真，本文件是对端消费者。
 */

// ---------- 内容块（messages.py） ----------

export interface TextBlock {
  type: "text";
  text: string;
  textSignature?: string | null;
}

export interface ThinkingBlock {
  type: "thinking";
  thinking: string;
  thinkingSignature?: string | null;
  redacted?: boolean;
}

export interface ImageBlock {
  type: "image";
  data: string;
  mimeType: string;
}

/** 助手发起的工具调用块（wire 字面量即 camelCase："toolCall"）。 */
export interface ToolCallBlock {
  type: "toolCall";
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  thoughtSignature?: string | null;
}

// ---------- 消息（messages.py，role 判别联合） ----------

export type StopReason = "stop" | "length" | "toolUse" | "error" | "aborted";

export interface UsageWire {
  input?: number;
  output?: number;
  cacheRead?: number;
  cacheWrite?: number;
  totalTokens?: number;
}

export interface UserMessageWire {
  role: "user";
  content: string | (TextBlock | ImageBlock)[]; // wire 两种形态并存（协议保真）
  timestamp: number;
}

export interface AssistantMessageWire {
  role: "assistant";
  content: (TextBlock | ThinkingBlock | ToolCallBlock)[];
  api?: string;
  provider?: string;
  model?: string;
  stopReason: StopReason;
  errorMessage?: string | null;
  usage?: UsageWire;
  timestamp: number;
}

export interface ToolResultMessageWire {
  role: "toolResult";
  toolCallId: string;
  toolName: string;
  content: (TextBlock | ImageBlock)[];
  details: unknown;
  addedToolNames?: string[] | null;
  isError: boolean;
  timestamp: number;
}

/** 以下四种 hastings 不会产生，但 get_messages 快照类型上必须完整（契约保真）。 */
export interface BashExecutionMessageWire {
  role: "bashExecution";
  command: string;
  output: string;
  exitCode?: number | null;
  timestamp: number;
}

export interface CustomMessageWire {
  role: "custom";
  customType: string;
  content: string | (TextBlock | ImageBlock)[];
  display?: boolean;
  details?: unknown;
  timestamp: number;
}

export interface BranchSummaryMessageWire {
  role: "branchSummary";
  summary: string;
  fromId: string;
  timestamp: number;
}

export interface CompactionSummaryMessageWire {
  role: "compactionSummary";
  summary: string;
  tokensBefore: number;
  timestamp: number;
}

export type AgentMessageWire =
  | UserMessageWire
  | AssistantMessageWire
  | ToolResultMessageWire
  | BashExecutionMessageWire
  | CustomMessageWire
  | BranchSummaryMessageWire
  | CompactionSummaryMessageWire;

// ---------- 工具结果载荷（tools.py AgentToolResult） ----------

export interface ToolResultPayloadWire {
  content: (TextBlock | ImageBlock)[];
  details?: unknown;
  addedToolNames?: string[] | null;
  terminate?: boolean | null;
}

// ---------- 助手流式事件（provider_events.py，嵌在 message_update 里） ----------

interface StreamPartial {
  partial: AssistantMessageWire;
}

export interface AssistantStartEvent {
  type: "start";
  partial: AssistantMessageWire;
}

export interface TextStartEvent extends StreamPartial {
  type: "text_start";
  contentIndex: number;
}

export interface TextDeltaEvent extends StreamPartial {
  type: "text_delta";
  contentIndex: number;
  delta: string;
}

export interface TextEndEvent extends StreamPartial {
  type: "text_end";
  contentIndex: number;
  content: string;
}

export interface ThinkingStartEvent extends StreamPartial {
  type: "thinking_start";
  contentIndex: number;
}

export interface ThinkingDeltaEvent extends StreamPartial {
  type: "thinking_delta";
  contentIndex: number;
  delta: string;
}

export interface ThinkingEndEvent extends StreamPartial {
  type: "thinking_end";
  contentIndex: number;
  content: string;
}

export interface ToolCallStartEvent extends StreamPartial {
  type: "toolcall_start";
  contentIndex: number;
}

export interface ToolCallDeltaEvent extends StreamPartial {
  type: "toolcall_delta";
  contentIndex: number;
  delta: string;
}

export interface ToolCallEndEvent extends StreamPartial {
  type: "toolcall_end";
  contentIndex: number;
  toolCall: ToolCallBlock;
}

export interface AssistantDoneEvent {
  type: "done";
  reason: "stop" | "length" | "toolUse";
  message: AssistantMessageWire;
}

export interface AssistantErrorEvent {
  type: "error";
  reason: "aborted" | "error";
  error: AssistantMessageWire;
}

export type AssistantMessageEventFrame =
  | AssistantStartEvent
  | TextStartEvent
  | TextDeltaEvent
  | TextEndEvent
  | ThinkingStartEvent
  | ThinkingDeltaEvent
  | ThinkingEndEvent
  | ToolCallStartEvent
  | ToolCallDeltaEvent
  | ToolCallEndEvent
  | AssistantDoneEvent
  | AssistantErrorEvent;

// ---------- Agent 事件帧（events.py AgentEvent 判别联合） ----------

export interface AgentStartEventFrame {
  type: "agent_start";
}

export interface AgentEndEventFrame {
  type: "agent_end";
  messages: AgentMessageWire[];
}

export interface TurnStartEventFrame {
  type: "turn_start";
}

export interface TurnEndEventFrame {
  type: "turn_end";
  message: AssistantMessageWire;
  toolResults: ToolResultMessageWire[];
}

export interface MessageStartEventFrame {
  type: "message_start";
  message: AgentMessageWire;
}

/** 别名字段 assistantMessageEvent 是流式渲染的生命线（P3 已有测试钉住）。 */
export interface MessageUpdateEventFrame {
  type: "message_update";
  message: AgentMessageWire;
  assistantMessageEvent: AssistantMessageEventFrame;
}

export interface MessageEndEventFrame {
  type: "message_end";
  message: AgentMessageWire;
}

export interface ToolExecutionStartEventFrame {
  type: "tool_execution_start";
  toolCallId: string;
  toolName: string;
  args: Record<string, unknown>;
}

export interface ToolExecutionUpdateEventFrame {
  type: "tool_execution_update";
  toolCallId: string;
  toolName: string;
  args: Record<string, unknown>;
  partialResult: ToolResultPayloadWire;
}

export interface ToolExecutionEndEventFrame {
  type: "tool_execution_end";
  toolCallId: string;
  toolName: string;
  result: ToolResultPayloadWire;
  isError: boolean;
}

export type AgentEventFrame =
  | AgentStartEventFrame
  | AgentEndEventFrame
  | TurnStartEventFrame
  | TurnEndEventFrame
  | MessageStartEventFrame
  | MessageUpdateEventFrame
  | MessageEndEventFrame
  | ToolExecutionStartEventFrame
  | ToolExecutionUpdateEventFrame
  | ToolExecutionEndEventFrame;

/** tau 契约内的全部事件类型（reducer 据此区分「已知 noop」与「未知上报」。 */
export const AGENT_EVENT_TYPES = [
  "agent_start",
  "agent_end",
  "turn_start",
  "turn_end",
  "message_start",
  "message_update",
  "message_end",
  "tool_execution_start",
  "tool_execution_update",
  "tool_execution_end",
] as const;

// ---------- 命令 / 响应帧（T6，hastings/server/ws.py D3 协议） ----------

export const COMMAND_TYPES = [
  "prompt",
  "steer",
  "follow_up",
  "abort",
  "get_state",
  "get_messages",
  "sync",
] as const;

export type CommandType = (typeof COMMAND_TYPES)[number];

/** 客户端 → 服务端命令帧。prompt/steer/follow_up 必须携带非空 message。 */
export interface CommandFrame {
  id: number | string;
  type: CommandType;
  message?: string;
  /** sync 命令（P5 T5）专用：目标章节 id，手动重跑该章最近一次修订的对账。 */
  chapter?: string;
}

/** 服务端 → 客户端响应帧（请求-响应配对靠 id 回显）。 */
export interface ResponseFrame {
  type: "response";
  command: CommandType | null;
  success: boolean;
  id?: number | string;
  data?: Record<string, unknown>;
  error?: string;
}

// ---------- 对账通知帧（P5 T4，hastings 协议层加法扩展，ws.py 同步） ----------

export type NoticeKind = "sync_queued" | "sync_start" | "sync_end";

/** 对账进度：保存成功后 queued → start → end（failed 时 UI 出重试）。 */
export interface NoticeFrame {
  type: "notice";
  kind: NoticeKind;
  chapter: string;
  hunks: number;
  updates: number;
  status?: "done" | "failed";
}

/** 服务端 → 客户端事件帧。 */
export type EventFrame = AgentEventFrame;

/** 服务端 → 客户端所有帧的收窄前形态。 */
export type ServerFrame = EventFrame | ResponseFrame | NoticeFrame;

export function isResponseFrame(frame: ServerFrame): frame is ResponseFrame {
  return frame.type === "response";
}

export function isNoticeFrame(frame: ServerFrame): frame is NoticeFrame {
  return frame.type === "notice";
}

// ---------- 文本提取辅助（对应 messages.py 的 .text property） ----------

function blockText(blocks: { type: string; text?: string }[]): string {
  return blocks
    .filter((b): b is TextBlock => b.type === "text" && typeof b.text === "string")
    .map((b) => b.text)
    .join("");
}

export function userMessageText(m: UserMessageWire | CustomMessageWire): string {
  return typeof m.content === "string" ? m.content : blockText(m.content);
}

export function assistantMessageText(m: AssistantMessageWire): string {
  return blockText(m.content);
}

export function assistantThinkingText(m: AssistantMessageWire): string {
  return m.content
    .filter((b): b is ThinkingBlock => b.type === "thinking")
    .map((b) => b.thinking)
    .join("");
}

export function toolResultPayloadText(p: ToolResultPayloadWire): string {
  return blockText(p.content);
}

export function toolResultMessageText(m: ToolResultMessageWire): string {
  return blockText(m.content);
}
