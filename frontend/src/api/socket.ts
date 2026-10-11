/**
 * WS 连接管理（P3 T8）：命令发送（自增 id + 响应配对 Promise）、连接后自动
 * 拉取快照（get_state + get_messages）、断线状态上报。重连是显式动作——
 * 不做自动重连风暴（计划 T8）；协议帧结构见同目录 types.ts。
 *
 * 错误原则与后端一致：响应 error 文案原样透传给调用方，不做人话翻译。
 */

import type {
  AgentMessageWire,
  CommandFrame,
  CommandType,
  EventFrame,
  NoticeFrame,
  ServerFrame,
  TokenUsageFrame,
  TokenUsagePayload,
} from "./types";
import { isNoticeFrame, isResponseFrame, isTokenUsageFrame } from "./types";

export type ConnectionStatus = "connecting" | "open" | "closed";

/** get_state 响应的 data 载荷（对应 ws.py `_state_data`）。 */
export interface AgentStateSnapshot {
  slug: string;
  model: string;
  is_running: boolean;
  tools: string[];
  /** 连接时即已知的上下文占用（P10 T12，D11）；老服务端缺该字段时为 null。 */
  contextUsage: TokenUsagePayload | null;
}

export interface SocketCallbacks {
  onStatus?(status: ConnectionStatus): void;
  onEvent?(event: EventFrame): void;
  /** 对账进度通知（P5 T4）：与 agent 事件分流，未注册时安静丢弃。 */
  onNotice?(notice: NoticeFrame): void;
  /** token 用量帧（P10 T12，D11）：每轮 agent_end 前推一帧，驱动对话头占用显示。 */
  onTokenUsage?(usage: TokenUsageFrame): void;
  /** get_state + get_messages 都到齐后回调一次（连接建立时自动拉取）。 */
  onSnapshot?(state: AgentStateSnapshot, messages: AgentMessageWire[]): void;
  /** 连接层错误（发送失败、响应超时、坏帧、快照结构不符）。 */
  onError?(message: string): void;
}

export interface CommandResult {
  data?: Record<string, unknown>;
}

/** 开发态经 Vite proxy（/ws → ws://localhost:8000），协议随页面自适应。 */
export function wsUrl(slug: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws/${encodeURIComponent(slug)}`;
}

interface Pending {
  command: CommandType;
  resolve: (result: CommandResult) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

function parseState(data: Record<string, unknown> | undefined): AgentStateSnapshot {
  const ok =
    data !== undefined &&
    typeof data["slug"] === "string" &&
    typeof data["model"] === "string" &&
    typeof data["is_running"] === "boolean" &&
    Array.isArray(data["tools"]);
  if (!ok) {
    throw new Error(
      `get_state 响应结构不符：期望含 slug/model/is_running/tools 字段的对象，` +
        `收到 ${JSON.stringify(data)}。为什么错：服务端协议或版本可能不匹配。` +
        `怎样算对：检查服务端 /ws 是否为 P3 协议后重连。`,
    );
  }
  const d = data as Record<string, unknown>;
  return {
    slug: d["slug"] as string,
    model: d["model"] as string,
    is_running: d["is_running"] as boolean,
    tools: (d["tools"] as unknown[]).map(String),
    contextUsage: parseTokenUsage(d["context_usage"]),
  };
}

/** context_usage 载荷宽容解析：缺字段/坏值回落 0（协议演进不崩首屏）。 */
function parseTokenUsage(raw: unknown): TokenUsagePayload | null {
  if (raw === null || raw === undefined || typeof raw !== "object") return null;
  const d = raw as Record<string, unknown>;
  const num = (key: string): number => (typeof d[key] === "number" ? (d[key] as number) : 0);
  return {
    input: num("input"),
    output: num("output"),
    total: num("total"),
    cache_read: num("cache_read"),
    cache_write: num("cache_write"),
    context_tokens: num("context_tokens"),
    window_tokens: num("window_tokens"),
    source: d["source"] === "provider_usage" ? "provider_usage" : "chars",
  };
}

function parseMessages(data: Record<string, unknown> | undefined): AgentMessageWire[] {
  if (data === undefined || !Array.isArray(data["messages"])) {
    throw new Error(
      `get_messages 响应结构不符：期望 { messages: [...] }，` +
        `收到 ${JSON.stringify(data)}。怎样算对：检查服务端 /ws 协议后重连。`,
    );
  }
  return data["messages"] as AgentMessageWire[];
}

export class AgentSocket {
  private ws: WebSocket | null = null;
  private nextId = 1;
  private readonly pending = new Map<number, Pending>();

  constructor(
    private readonly url: string,
    private readonly callbacks: SocketCallbacks = {},
    private readonly timeoutMs = 15_000,
  ) {}

  connect(): void {
    this.callbacks.onStatus?.("connecting");
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => {
      this.callbacks.onStatus?.("open");
      void this.loadSnapshot();
    };
    ws.onclose = () => {
      this.callbacks.onStatus?.("closed");
      // 被动断线（服务重启/网络中断）同样清理未决命令（R3 问题3）——
      // 否则在途命令悬挂至超时且误报「响应超时」，归因违反错误三要素
      this.failPending("连接已断开");
    };
    ws.onerror = () =>
      this.callbacks.onError?.("WebSocket 连接出错（服务未启动或地址不对）。怎样算对：确认服务已启动后点击重连。");
    ws.onmessage = (ev: MessageEvent) => this.handleText(ev.data as string);
  }

  close(): void {
    // 先清未决再关连接：保证主动关闭的 reject 归因「连接已关闭」，
    // 不被 onclose 的「连接已断开」抢先（FakeWebSocket 同步回调时尤甚）
    this.failPending("连接已关闭");
    this.ws?.close();
    this.ws = null;
  }

  /** 清空未决命令并 reject：主动 close 与被动断线共用（R3 问题3）。 */
  private failPending(headline: string): void {
    for (const [id, entry] of this.pending) {
      clearTimeout(entry.timer);
      entry.reject(
        new Error(`${headline}，命令 ${entry.command}（id=${id}）不再等待响应。怎样算对：重连后重试。`),
      );
    }
    this.pending.clear();
  }

  /** 发送命令帧并等待同 id 响应帧；响应 error 文案随异常透传。 */
  command(type: CommandType, message?: string, chapter?: string): Promise<CommandResult> {
    const ws = this.ws;
    if (ws === null || ws.readyState !== WebSocket.OPEN) {
      return Promise.reject(
        new Error(
          `命令 ${type} 无法发送：WebSocket 未连接（state=${ws?.readyState ?? "null"}）。` +
            `怎样算对：等状态变为已连接后重试，或点击重连。`,
        ),
      );
    }
    const id = this.nextId++;
    // 只写实际携带的字段：prompt/steer/follow_up 用 message，sync 用 chapter。
    const frame: CommandFrame = { id, type };
    if (message !== undefined) frame.message = message;
    if (chapter !== undefined) frame.chapter = chapter;
    return new Promise<CommandResult>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(
          new Error(
            `命令 ${type}（id=${id}）等待响应超时（${this.timeoutMs}ms）。` +
              `为什么错：服务端未在期限内回帧。怎样算对：检查服务是否存活后重试。`,
          ),
        );
      }, this.timeoutMs);
      this.pending.set(id, { command: type, resolve, reject, timer });
      ws.send(JSON.stringify(frame));
    });
  }

  private async loadSnapshot(): Promise<void> {
    try {
      const [stateRes, msgsRes] = await Promise.all([
        this.command("get_state"),
        this.command("get_messages"),
      ]);
      this.callbacks.onSnapshot?.(parseState(stateRes.data), parseMessages(msgsRes.data));
    } catch (exc) {
      this.callbacks.onError?.(exc instanceof Error ? exc.message : String(exc));
    }
  }

  private handleText(text: string): void {
    let frame: ServerFrame;
    try {
      frame = JSON.parse(text) as ServerFrame;
    } catch {
      this.callbacks.onError?.(`收到无法解析的帧：${text.slice(0, 200)}`);
      return;
    }
    if (isNoticeFrame(frame)) {
      this.callbacks.onNotice?.(frame);
      return;
    }
    if (isTokenUsageFrame(frame)) {
      this.callbacks.onTokenUsage?.(frame);
      return;
    }
    if (!isResponseFrame(frame)) {
      this.callbacks.onEvent?.(frame as EventFrame);
      return;
    }
    // 无法配对的响应（无 id / 未知 id / 超时后才到）只能忽略，不误配对
    if (frame.id === undefined) return;
    const entry = this.pending.get(frame.id as number);
    if (!entry) return;
    clearTimeout(entry.timer);
    this.pending.delete(frame.id as number);
    if (frame.success) entry.resolve({ data: frame.data });
    else entry.reject(new Error(frame.error ?? `命令 ${entry.command} 失败（服务端未给出原因）。`));
  }
}
