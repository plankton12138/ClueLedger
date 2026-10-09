/**
 * AgentSocket 剧本测试（P3 T8）——与后端 test_server_ws.py 对称：
 * FakeWebSocket 桩掉传输层，断言「命令帧出、事件帧入、响应配对」三件事。
 * 「事件序列进、断言出」的 FakeProvider 哲学在传输层的对应物。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { AgentSocket, type ConnectionStatus, type SocketCallbacks } from "./socket";
import type { EventFrame } from "./types";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static OPEN = 1;
  static CLOSED = 3;
  url: string;
  readyState = 0; // CONNECTING
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  simulateOpen(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  receive(frame: unknown): void {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }

  sentFrames(): Array<Record<string, unknown>> {
    return this.sent.map((s) => JSON.parse(s) as Record<string, unknown>);
  }
}

vi.stubGlobal("WebSocket", FakeWebSocket);

interface Fixture {
  agent: AgentSocket;
  ws: FakeWebSocket;
  statuses: ConnectionStatus[];
  events: EventFrame[];
  snapshots: Array<{ state: unknown; messages: unknown[] }>;
  errors: string[];
}

function makeSocket(opts?: Partial<SocketCallbacks> & { timeoutMs?: number }): Fixture {
  const { timeoutMs, ...callbacks } = opts ?? {};
  const fixture: Fixture = {
    agent: null as unknown as AgentSocket,
    ws: null as unknown as FakeWebSocket,
    statuses: [],
    events: [],
    snapshots: [],
    errors: [],
  };
  fixture.agent = new AgentSocket(
    "ws://test/ws",
    {
      onStatus: (s) => fixture.statuses.push(s),
      onEvent: (e) => fixture.events.push(e),
      onSnapshot: (state, messages) => fixture.snapshots.push({ state, messages }),
      onError: (m) => fixture.errors.push(m),
      ...callbacks,
    },
    timeoutMs,
  );
  fixture.agent.connect(); // WebSocket 在 connect() 时才构造，桩实例此刻才有
  fixture.ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  return fixture;
}

const STATE_DATA = {
  slug: "demo-story",
  model: "fake-model",
  is_running: false,
  tools: ["bible_upsert", "write_chapter"],
};

/** onSnapshot 在响应 resolve 后的微任务链里触发，断言前先排空队列。 */
function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  FakeWebSocket.instances = [];
});

describe("AgentSocket 连接生命周期", () => {
  it("connect 后状态两跳，且自动发送 get_state 与 get_messages 快照命令", () => {
    const { ws, statuses } = makeSocket(); // makeSocket 内部已完成 connect
    expect(statuses).toEqual(["connecting"]);
    ws.simulateOpen();
    expect(statuses).toEqual(["connecting", "open"]);

    const frames = ws.sentFrames();
    expect(frames.map((f) => f["type"])).toEqual(["get_state", "get_messages"]);
    expect(frames[0]["id"]).toBe(1);
    expect(frames[1]["id"]).toBe(2);
  });

  it("close 后状态回到 closed，后续命令拒绝", async () => {
    const { agent, ws, statuses } = makeSocket();
    ws.simulateOpen();
    agent.close();
    expect(statuses[statuses.length - 1]).toBe("closed");
    await expect(agent.command("abort")).rejects.toThrow("未连接");
  });

  it("被动断线：在途命令立即 reject 且归因断线（R3 问题3，非误报超时）", async () => {
    const { agent, ws } = makeSocket();
    ws.simulateOpen();
    const p = agent.command("abort");
    ws.readyState = FakeWebSocket.CLOSED;
    ws.onclose?.(); // 模拟服务重启/网络中断——非 agent.close() 主动关闭
    await expect(p).rejects.toThrow("连接已断开");
  });
});

describe("快照回填", () => {
  it("两个响应都到齐才触发一次 onSnapshot，messages 透传", async () => {
    const { ws, snapshots } = makeSocket();
    ws.simulateOpen();

    ws.receive({ type: "response", command: "get_state", success: true, id: 1, data: STATE_DATA });
    await flush();
    expect(snapshots).toHaveLength(0); // 只差 get_messages，还不能回填

    ws.receive({
      type: "response",
      command: "get_messages",
      success: true,
      id: 2,
      data: { messages: [{ role: "user", content: "建角色", timestamp: 5 }] },
    });
    await flush();
    expect(snapshots).toHaveLength(1);
    expect(snapshots[0].state).toEqual(STATE_DATA);
    expect(snapshots[0].messages).toEqual([{ role: "user", content: "建角色", timestamp: 5 }]);
  });

  it("get_state 响应结构坏了 → onError 报三要素，不静默", async () => {
    const { ws, errors } = makeSocket();
    ws.simulateOpen();
    ws.receive({ type: "response", command: "get_state", success: true, id: 1, data: { foo: 1 } });
    ws.receive({ type: "response", command: "get_messages", success: true, id: 2, data: { messages: [] } });
    await flush();
    expect(errors).toHaveLength(1);
    expect(errors[0]).toContain("get_state");
    expect(errors[0]).toContain("slug");
  });
});

describe("命令发送与响应配对", () => {
  it("自增 id 出帧；事件帧进 onEvent 不干扰配对；响应按 id resolve", async () => {
    const { agent, ws, events } = makeSocket();
    ws.simulateOpen();

    const p = agent.command("prompt", "建一个角色");
    const frames = ws.sentFrames();
    const promptFrame = frames[frames.length - 1] as Record<string, unknown>;
    expect(promptFrame).toEqual({ id: 3, type: "prompt", message: "建一个角色" });

    ws.receive({ type: "agent_start" }); // 事件帧先到
    ws.receive({ type: "response", command: "prompt", success: true, id: 3, data: { started: true } });
    await expect(p).resolves.toEqual({ data: { started: true } });
    expect(events).toEqual([{ type: "agent_start" }]);
  });

  it("错误响应 reject，error 文案原样透传（不做人话翻译）", async () => {
    const { agent, ws } = makeSocket();
    ws.simulateOpen();

    const p = agent.command("prompt", "插队");
    ws.receive({ type: "response", command: "prompt", success: false, id: 3, error: "agent 正在写作中，请用 steer 或 follow_up" });
    await expect(p).rejects.toThrow("agent 正在写作中，请用 steer 或 follow_up");
  });

  it("响应超时 reject（防永久 pending）", async () => {
    vi.useFakeTimers();
    const { agent, ws } = makeSocket({ timeoutMs: 50 });
    ws.simulateOpen();

    const p = agent.command("abort");
    const assertion = expect(p).rejects.toThrow("响应超时");
    vi.advanceTimersByTime(60);
    await assertion;
    vi.useRealTimers();
  });

  it("close 后未决命令全部 reject（不留悬挂 Promise）", async () => {
    const { agent, ws } = makeSocket();
    ws.simulateOpen();
    const p = agent.command("abort");
    agent.close();
    await expect(p).rejects.toThrow("连接已关闭");
  });

  it("坏 JSON 帧不崩，走 onError 上报", () => {
    const { ws, errors } = makeSocket();
    ws.simulateOpen();
    ws.onmessage?.({ data: "{not-json" });
    expect(errors).toHaveLength(1);
    expect(errors[0]).toContain("无法解析");
  });

  it("无 id / 迟到的响应帧被忽略，不误配对", async () => {
    const { agent, ws } = makeSocket();
    ws.simulateOpen();
    const p = agent.command("abort"); // id=3
    ws.receive({ type: "response", command: "abort", success: true, id: 3, data: { wrong: true } });
    ws.receive({ type: "response", command: "abort", success: true }); // 无 id
    ws.receive({ type: "response", command: "abort", success: true, id: 99 }); // 未知 id
    await expect(p).resolves.toEqual({ data: { wrong: true } });
  });

  it("sync 命令携带 chapter 字段（不复用 message）", async () => {
    const { agent, ws } = makeSocket();
    ws.simulateOpen();
    void agent.command("sync", undefined, "ch03");
    const frames = ws.sentFrames();
    expect(frames[frames.length - 1]).toEqual({ id: 3, type: "sync", chapter: "ch03" });
  });
});

describe("notice 帧分流（P5 T5）", () => {
  it("notice 帧进 onNotice，不进 onEvent（与 agent 事件分流）", () => {
    const notices: unknown[] = [];
    const { ws, events } = makeSocket({ onNotice: (n) => notices.push(n) });
    ws.simulateOpen();
    ws.receive({
      type: "notice",
      kind: "sync_end",
      chapter: "ch03",
      hunks: 2,
      updates: 1,
      status: "done",
    });
    expect(notices).toEqual([
      { type: "notice", kind: "sync_end", chapter: "ch03", hunks: 2, updates: 1, status: "done" },
    ]);
    expect(events).toEqual([]);
  });
});
