import { describe, expect, it } from "vitest";
import type {
  AgentEventFrame,
  AssistantMessageWire,
  MessageUpdateEventFrame,
  TextDeltaEvent,
  ThinkingDeltaEvent,
  ToolCallBlock,
  ToolResultMessageWire,
  UserMessageWire,
} from "../api/types";
import { applyEvent, applyMessages, initialUiState } from "./reducer";

// --- 剧本构造器：与 test_server_ws.py 的 fake_scripts 同源，字段名按 wire 别名 ---

function userMsg(text: string): UserMessageWire {
  return { role: "user", content: text, timestamp: 0 };
}

function assistantMsg(
  text: string,
  opts: {
    toolCalls?: ToolCallBlock[];
    stopReason?: AssistantMessageWire["stopReason"];
    errorMessage?: string;
    thinking?: string;
  } = {},
): AssistantMessageWire {
  const content: AssistantMessageWire["content"] = [];
  if (opts.thinking) content.push({ type: "thinking", thinking: opts.thinking });
  if (text) content.push({ type: "text", text });
  if (opts.toolCalls) content.push(...opts.toolCalls);
  return {
    role: "assistant",
    content,
    stopReason: opts.stopReason ?? "stop",
    errorMessage: opts.errorMessage ?? null,
    timestamp: 0,
  };
}

function toolCall(id: string, name: string): ToolCallBlock {
  return { type: "toolCall", id, name, arguments: {} };
}

function textDelta(delta: string): MessageUpdateEventFrame {
  const nested: TextDeltaEvent = {
    type: "text_delta",
    contentIndex: 0,
    delta,
    partial: assistantMsg(delta),
  };
  return { type: "message_update", message: assistantMsg(delta), assistantMessageEvent: nested };
}

function thinkingDelta(delta: string): MessageUpdateEventFrame {
  const nested: ThinkingDeltaEvent = {
    type: "thinking_delta",
    contentIndex: 0,
    delta,
    partial: assistantMsg(""),
  };
  return { type: "message_update", message: assistantMsg(""), assistantMessageEvent: nested };
}

function toolResultMsg(toolCallId: string, isError: boolean): ToolResultMessageWire {
  return {
    role: "toolResult",
    toolCallId,
    toolName: "character_upsert",
    content: [{ type: "text", text: isError ? "被拒" : "已登记" }],
    details: isError ? null : { id: "pol-1" },
    isError,
    timestamp: 0,
  };
}

// --- ① 流式：text_delta 拼接 + MessageEnd 定稿 ---

describe("流式渲染", () => {
  it("text_delta 逐段进 buffer，message_end 以 canonical 消息定稿", () => {
    let s = applyEvent(initialUiState, { type: "message_start", message: assistantMsg("") });
    s = applyEvent(s, textDelta("先建档。"));
    s = applyEvent(s, textDelta(" 他是比利时侦探。"));
    expect(s.assistantBuffer).toBe("先建档。 他是比利时侦探。");
    expect(s.items).toHaveLength(0); // 流式中不产生定稿条目

    s = applyEvent(s, { type: "message_end", message: assistantMsg("先建档。 他是比利时侦探。") });

    expect(s.assistantBuffer).toBe("");
    expect(s.items).toEqual([{ kind: "assistant", text: "先建档。 他是比利时侦探。" }]);
  });

  it("thinking_delta 进独立 buffer，定稿产出 thinking 条目（不与正文混流）", () => {
    let s = applyEvent(initialUiState, { type: "message_start", message: assistantMsg("") });
    s = applyEvent(s, thinkingDelta("先查账本。"));
    s = applyEvent(s, textDelta("正文开始。"));
    expect(s.thinkingBuffer).toBe("先查账本。");
    expect(s.assistantBuffer).toBe("正文开始。");

    s = applyEvent(s, {
      type: "message_end",
      message: assistantMsg("正文开始。", { thinking: "先查账本。" }),
    });

    expect(s.thinkingBuffer).toBe("");
    expect(s.items).toEqual([
      { kind: "thinking", text: "先查账本。" },
      { kind: "assistant", text: "正文开始。" },
    ]);
  });

  it("applyEvent 是纯函数：不改动传入的旧状态", () => {
    const before = initialUiState;
    applyEvent(before, { type: "agent_start" });
    expect(before.running).toBe(false);
    expect(before.items).toHaveLength(0);
  });
});

// --- ② 工具卡片生命周期：start→update→end，error 卡片 ---

describe("工具卡片生命周期", () => {
  const call = toolCall("call-w1", "character_upsert");

  function runToRunningCard() {
    let s = applyEvent(initialUiState, { type: "agent_start" });
    s = applyEvent(s, {
      type: "message_end",
      message: assistantMsg("", { toolCalls: [call], stopReason: "toolUse" }),
    });
    s = applyEvent(s, {
      type: "tool_execution_start",
      toolCallId: call.id,
      toolName: call.name,
      args: { name: "波洛" },
    });
    return s;
  }

  it("start 建卡（running）→ end 成功收卡（ok + 结果文本 + details）", () => {
    let s = runToRunningCard();
    const card = s.items[0];
    expect(card).toMatchObject({ kind: "tool", toolCallId: "call-w1", status: "running" });

    s = applyEvent(s, {
      type: "tool_execution_end",
      toolCallId: call.id,
      toolName: call.name,
      result: {
        content: [{ type: "text", text: "已登记：波洛（比利时侦探）" }],
        details: { id: "pol-1", version: 1 },
      },
      isError: false,
    });

    expect(s.items[0]).toEqual({
      kind: "tool",
      toolCallId: "call-w1",
      toolName: "character_upsert",
      args: { name: "波洛" },
      status: "ok",
      resultText: "已登记：波洛（比利时侦探）",
      resultDetails: { id: "pol-1", version: 1 },
    });
    expect(s.running).toBe(true); // 工具收卡不影响 running，agent_end 才收
  });

  it("end is_error=true → error 卡片（工具层守门结果对用户可见）", () => {
    let s = runToRunningCard();
    s = applyEvent(s, {
      type: "tool_execution_end",
      toolCallId: call.id,
      toolName: call.name,
      result: {
        content: [{ type: "text", text: "参数违规：name 不能为空（loc: name）" }],
        details: null,
      },
      isError: true,
    });

    expect(s.items[0]).toMatchObject({
      kind: "tool",
      status: "error",
      resultText: "参数违规：name 不能为空（loc: name）",
    });
  });

  it("update 把流式 partial 结果写进卡片，不改变状态", () => {
    let s = runToRunningCard();
    s = applyEvent(s, {
      type: "tool_execution_update",
      toolCallId: call.id,
      toolName: call.name,
      args: {},
      partialResult: { content: [{ type: "text", text: "写入中…" }], details: null },
    });
    expect(s.items[0]).toMatchObject({ status: "running", resultText: "写入中…" });
  });

  it("正文与工具共存时顺序保真：text 在前、卡片在后", () => {
    let s = applyEvent(initialUiState, { type: "message_start", message: assistantMsg("") });
    s = applyEvent(s, textDelta("先建档。"));
    s = applyEvent(s, { type: "message_end", message: assistantMsg("先建档。", { toolCalls: [call], stopReason: "toolUse" }) });
    s = applyEvent(s, { type: "tool_execution_start", toolCallId: call.id, toolName: call.name, args: {} });

    expect(s.items.map((i) => i.kind)).toEqual(["assistant", "tool"]);
    expect(s.items[0]).toMatchObject({ kind: "assistant", text: "先建档。" });
  });
});

// --- ③ running 状态与 assistant 错误卡片 ---

describe("运行状态与错误", () => {
  it("agent_start 置 running 并清错误，agent_end 收 running", () => {
    let s = applyEvent(initialUiState, { type: "agent_start" });
    expect(s.running).toBe(true);
    s = applyEvent(s, { type: "agent_end", messages: [] });
    expect(s.running).toBe(false);
  });

  it("assistant stopReason=error/aborted → error 卡片（errorMessage 优先）", () => {
    let s = applyEvent(initialUiState, { type: "message_start", message: assistantMsg("") });
    s = applyEvent(s, {
      type: "message_end",
      message: assistantMsg("", { stopReason: "error", errorMessage: "provider 连接中断" }),
    });
    expect(s.items).toEqual([{ kind: "error", text: "provider 连接中断" }]);
  });
});

// --- ④ 未知事件上报（PlainRenderer 惯例：显式暴露不静默） ---

describe("未知事件上报", () => {
  it("契约外事件进 unhandledEventTypes 且不崩溃；已知 noop 事件不误报", () => {
    let s = applyEvent(initialUiState, { type: "turn_start" }); // 已知，无 UI 效果
    s = applyEvent(s, {
      type: "turn_end",
      message: assistantMsg("ok"),
      toolResults: [],
    });
    s = applyEvent(s, { type: "agent_settled" } as unknown as AgentEventFrame); // session 层事件，P3 契约外

    expect(s.unhandledEventTypes).toEqual(["agent_settled"]);
    expect(s.eventLog).toHaveLength(3); // 调试视图保留全部原始帧
  });

  it("同一未知类型只上报一次（去重，不刷屏）", () => {
    let s = applyEvent(initialUiState, { type: "mystery" } as unknown as AgentEventFrame);
    s = applyEvent(s, { type: "mystery" } as unknown as AgentEventFrame);
    expect(s.unhandledEventTypes).toEqual(["mystery"]);
  });
});

// --- ⑤ get_messages 快照重放（浏览器刷新恢复路径） ---

describe("快照重放 applyMessages", () => {
  it("user/assistant/toolResult 三种消息还原为对话流条目", () => {
    const s = applyMessages(initialUiState, [
      userMsg("建一个角色"),
      assistantMsg("先建档。", { toolCalls: [toolCall("call-w1", "character_upsert")] }),
      toolResultMsg("call-w1", false),
      assistantMsg("建档完成。"),
    ]);

    expect(s.items).toEqual([
      { kind: "user", text: "建一个角色" },
      { kind: "assistant", text: "先建档。" },
      {
        kind: "tool",
        toolCallId: "call-w1",
        toolName: "character_upsert",
        args: {},
        status: "ok",
        resultText: "已登记",
        resultDetails: { id: "pol-1" },
      },
      { kind: "assistant", text: "建档完成。" },
    ]);
  });

  it("is_error 的工具结果还原为 error 卡片；契约外 role 记入 unhandled", () => {
    const s = applyMessages(initialUiState, [
      toolResultMsg("call-x", true),
      { role: "custom", customType: "note", content: "hi", timestamp: 0 },
    ]);
    expect(s.items[0]).toMatchObject({ kind: "tool", status: "error", resultText: "被拒" });
    expect(s.unhandledEventTypes).toEqual(["message:custom"]);
  });
});
