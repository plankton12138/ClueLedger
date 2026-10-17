/**
 * 对话状态契约测试（Phase 19.2）。
 *
 * 计划 §19.2「先写失败测试」清单与交付项：
 * 1. 跨项目晚到事件不能污染当前界面；
 * 2. 同一分片去重；
 * 3. 停止后迟到分片无效；
 * 4. 切页/切会话保留输入草稿；
 * 5. 模型切换不改已有轮次标签；
 * 6. 规范化索引：按 project/conversation/turn 分桶，流式消息不进正文缓冲；
 * 7. 发送结果未知：保留幂等键，用同一请求重试不重复调用模型；
 * 8. 草稿落浏览器存储；
 * 9. 服务选择与能力探测（缺少端点 → 重启指引）；
 * 10. 断线重连后以服务端为准补偿。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  applyChatEvent,
  emptyView,
  loadDrafts,
  saveDrafts,
  selectChatService,
  useChatStore,
  type ConversationView,
} from "./chat";
import type {
  ChatEventEnvelope,
  ChatMessage,
  ChatService,
  ChatTurn,
} from "../api/chat";
import { TaskKind } from "../api/chat";

function envelope(
  projectId: string,
  conversationId: string,
  event: ChatEventEnvelope["event"],
  sequence = 0,
): ChatEventEnvelope {
  return { project_id: projectId, conversation_id: conversationId, turn_id: null, sequence, event };
}

/** 注入受控服务：跳过能力探测，直接装配替身（生产路径由 selectChatService 测试覆盖）。 */
function apiSelection(service: ChatService) {
  return {
    mock: false,
    probe: async () => ({
      supported: true,
      protocol: "chat.v1",
      reason: "ok" as const,
      detail: "",
    }),
    createApi: () => service,
  };
}

const turn = (id: string, status: ChatTurn["status"], model = "模型A"): ChatTurn => ({
  turn_id: id,
  conversation_id: "c1",
  status,
  model,
  end_reason: null,
  idempotency_key: `idem-${id}`,
  retry_of: null,
});

const message = (id: string, over: Partial<ChatMessage> = {}): ChatMessage => ({
  message_id: id,
  conversation_id: "c1",
  turn_id: "t1",
  role: "assistant",
  kind: "text",
  text: "",
  refs: [],
  model: "模型A",
  status: "streaming",
  error: null,
  created_at: "2026-10-16T00:00:00Z",
  ...over,
});

function fakeService(over: Partial<ChatService> = {}): ChatService {
  return {
    kind: "api",
    capabilities: () => ({
      protocol: "chat.v1",
      streaming: true,
      task_drafts: true,
      simulated: false,
    }),
    currentModel: () => "模型A",
    listConversations: async () => [],
    createConversation: async () => ({
      conversation_id: "c1",
      project_id: "p1",
      title: "新对话",
      created_at: "",
      updated_at: "",
    }),
    loadMessages: async () => [],
    sendTurn: async () => ({ turn: turn("t1", "queued"), user_message: message("m1") }),
    continueTurn: async (_p, _c, input) => ({
      turn: turn("t9", "queued"),
      user_message: message(input.source_message_ids[0] ?? "m1", {
        role: "author",
        status: "completed",
      }),
    }),
    cancelTurn: async () => undefined,
    retryTurn: async () => turn("t2", "queued"),
    adjustDraft: async () => {
      throw new Error("not used");
    },
    draftFromMessage: async () => {
      throw new Error("not used");
    },
    createDraft: async () => {
      throw new Error("not used");
    },
    abandonDraft: async () => undefined,
    startDraft: async () => ({ job_ref: "job" }),
    retryTask: async () => undefined,
    acceptCandidate: async () => ({
      job_ref: "job",
      commit_id: "c",
      target_ids: [],
      summary: "",
    }),
    cancelTask: async () => undefined,
    subscribe: () => () => undefined,
    ...over,
  };
}

/** node 测试环境没有 localStorage：用内存桩验证持久化读写。 */
function installStorage() {
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  });
}

beforeEach(() => {
  installStorage();
  useChatStore.setState({
    serviceKind: "probing",
    capabilityDetail: "",
    protocol: null,
    projectId: null,
    conversationsByProject: {},
    activeConversationId: null,
    views: {},
    drafts: {},
    refs: {},
    unread: 0,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  try {
    localStorage.clear();
  } catch {
    /* node 环境无 localStorage 时忽略 */
  }
});

describe("事件 reducer（纯函数）", () => {
  it("按信封序号去重，且不修改入参", () => {
    const view: ConversationView = {
      ...emptyView(),
      turns: { t1: turn("t1", "running") },
      messages: [message("m1", { status: "streaming" })],
    };
    const first = applyChatEvent(
      view,
      envelope("p1", "c1", { type: "message.delta", turn_id: "t1", text: "甲" }, 1),
    );
    const dup = applyChatEvent(
      first,
      envelope("p1", "c1", { type: "message.delta", turn_id: "t1", text: "甲" }, 1),
    );
    expect(first.messages[0]!.text).toBe("甲");
    expect(first.cursor).toBe(1);
    // 重复分片不再追加；原 view 未被修改（纯函数）。
    expect(dup.messages[0]!.text).toBe("甲");
    expect(view.messages[0]!.text).toBe("");
    expect(view.cursor).toBe(0);
  });

  it("终态之后的迟到分片无效；终态文本以完成事件为准", () => {
    const running: ConversationView = {
      ...emptyView(),
      turns: { t1: turn("t1", "running") },
      messages: [message("m1")],
    };
    const cancelled = applyChatEvent(
      running,
      envelope("p1", "c1", { type: "turn.cancelled", turn_id: "t1", reason: "作者停止回复" }, 2),
    );
    const late = applyChatEvent(
      cancelled,
      envelope("p1", "c1", { type: "message.delta", turn_id: "t1", text: "迟到" }, 3),
    );
    expect(cancelled.turns["t1"]!.status).toBe("stopped");
    expect(cancelled.messages[0]!.status).toBe("stopped");
    expect(late.messages[0]!.text).toBe("");
    // 终态序号单调推进，迟到分片不推进游标。
    expect(late.cursor).toBe(2);
    expect(late.activeTurnId).toBeNull();
  });

  it("turn.started 建流式占位；message.completed 以完整正文收束", () => {
    const started = applyChatEvent(
      { ...emptyView(), turns: { t1: turn("t1", "queued") } },
      envelope(
        "p1",
        "c1",
        { type: "turn.started", turn_id: "t1", message_id: "m1", model: "模型A" },
        1,
      ),
    );
    expect(started.messages).toHaveLength(1);
    expect(started.messages[0]!.status).toBe("streaming");
    const done = applyChatEvent(
      applyChatEvent(
        started,
        envelope("p1", "c1", { type: "message.delta", turn_id: "t1", text: "片段" }, 2),
      ),
      envelope(
        "p1",
        "c1",
        { type: "message.completed", turn_id: "t1", text: "完整正文", end_reason: "stop" },
        3,
      ),
    );
    expect(done.messages[0]!.text).toBe("完整正文");
    expect(done.messages[0]!.status).toBe("completed");
    expect(done.activeTurnId).toBeNull();
  });

  it("失败轮次记录错误三要素，保留已收到的部分回复", () => {
    const failed = applyChatEvent(
      {
        ...emptyView(),
        turns: { t1: turn("t1", "running") },
        messages: [message("m1", { text: "已收到一半" })],
      },
      envelope(
        "p1",
        "c1",
        {
          type: "turn.failed",
          turn_id: "t1",
          reason: "连接中断",
          error: {
            code: "CHAT_PROVIDER_UNAVAILABLE",
            category: "environment",
            where: "chat.turn",
            why: "连接中断",
            fix: "重试这一轮",
          },
        },
        2,
      ),
    );
    expect(failed.turns["t1"]!.status).toBe("failed");
    expect(failed.messages[0]!.status).toBe("failed");
    expect(failed.messages[0]!.text).toBe("已收到一半");
    expect(failed.messages[0]!.error!.fix).toBe("重试这一轮");
  });
});

describe("会话隔离与索引", () => {
  it("不同会话的消息互不影响，切回不丢历史", async () => {
    const store = useChatStore.getState();
    const service = fakeService({
      listConversations: async () => [
        { conversation_id: "c1", project_id: "p1", title: "甲", created_at: "", updated_at: "" },
        { conversation_id: "c2", project_id: "p1", title: "乙", created_at: "", updated_at: "" },
      ],
      loadMessages: async (_p, cid) => [
        message(`${cid}-m1`, { status: "completed", text: `${cid} 的历史`, conversation_id: cid }),
      ],
    });
    await store.bindProject("p1", apiSelection(service));
    // 绑定项目后自动选中最近会话，避免多点一次。
    expect(useChatStore.getState().activeConversationId).toBe("c1");
    await useChatStore.getState().selectConversation("c2");
    const views = useChatStore.getState().views;
    expect(views.c1!.messages[0]!.text).toBe("c1 的历史");
    expect(views.c2!.messages[0]!.text).toBe("c2 的历史");
    expect(useChatStore.getState().views.c1!.messages[0]!.text).toBe("c1 的历史");
    // 每个会话独立游标。
    expect(useChatStore.getState().views.c1!.cursor).toBe(0);
  });

  it("跨项目/跨会话晚到事件不污染当前视图", async () => {
    useChatStore.setState({
      projectId: "p1",
      activeConversationId: "c1",
      views: { c1: { ...emptyView(), turns: { t1: turn("t1", "running") }, messages: [message("m1")] } },
    });
    const apply = useChatStore.getState().applyEvent;
    apply(envelope("p2", "c1", { type: "message.delta", turn_id: "t1", text: "污染" }, 1));
    apply(envelope("p1", "c9", { type: "message.delta", turn_id: "t1", text: "污染" }, 1));
    expect(useChatStore.getState().views.c1!.messages[0]!.text).toBe("");
    expect(useChatStore.getState().views.c9).toBeUndefined();
  });
});

describe("草稿与轮次模型", () => {
  it("草稿按会话分桶并落浏览器存储", () => {
    useChatStore.setState({ activeConversationId: "c1" });
    useChatStore.getState().setDraft("第一个会话的草稿");
    useChatStore.setState({ activeConversationId: "c2" });
    useChatStore.getState().setDraft("第二个会话的草稿");
    saveDrafts(useChatStore.getState().drafts);
    expect(loadDrafts()).toEqual({
      c1: "第一个会话的草稿",
      c2: "第二个会话的草稿",
    });
  });

  it("受控草稿读取：空存储返回空对象，坏数据不抛异常", () => {
    expect(loadDrafts()).toEqual({});
    localStorage.setItem("hastings.chat.drafts", "not-json");
    expect(loadDrafts()).toEqual({});
    localStorage.setItem("hastings.chat.drafts", '[1,2]');
    expect(loadDrafts()).toEqual({});
  });

  it("轮次模型在发送时定格，切换模型不改已有轮次", async () => {
    const sent: string[] = [];
    let model = "模型A";
    const service = fakeService({
      currentModel: () => model,
      listConversations: async () => [
        { conversation_id: "c1", project_id: "p1", title: "甲", created_at: "", updated_at: "" },
      ],
      sendTurn: async (_p, _c, input) => {
        sent.push(`${model}:${input.idempotency_key}`);
        return {
          turn: { ...turn(`t${sent.length}`, "queued", model), idempotency_key: input.idempotency_key },
          user_message: message(`m${sent.length}`, { role: "author", text: input.text }),
        };
      },
    });
    await useChatStore.getState().bindProject("p1", apiSelection(service));
    useChatStore.getState().setDraft("第一轮");
    await useChatStore.getState().send();
    const first = useChatStore.getState().views.c1!.turns.t1!;
    expect(first.model).toBe("模型A");
    // 第一轮收束后才允许发下一条（同会话不并发两轮）。
    useChatStore.getState().applyEvent({
      project_id: "p1",
      conversation_id: "c1",
      turn_id: "t1",
      sequence: 1,
      event: {
        type: "message.completed",
        turn_id: "t1",
        text: "第一轮回复",
        end_reason: "stop",
      },
    });
    expect(useChatStore.getState().views.c1!.activeTurnId).toBeNull();
    model = "模型B";
    useChatStore.getState().setDraft("第二轮");
    await useChatStore.getState().send();
    expect(useChatStore.getState().views.c1!.turns.t2!.model).toBe("模型B");
    expect(useChatStore.getState().views.c1!.turns.t1!.model).toBe("模型A");
  });
});

describe("发送结果未知与幂等重试", () => {
  it("网络故障保留幂等键与草稿，重试用同一请求（不重复调用模型）", async () => {
    const keys: string[] = [];
    let fail = true;
    const service = fakeService({
      listConversations: async () => [
        { conversation_id: "c1", project_id: "p1", title: "甲", created_at: "", updated_at: "" },
      ],
      sendTurn: async (_p, _c, input) => {
        keys.push(input.idempotency_key);
        if (fail) throw new TypeError("Failed to fetch");
        return {
          turn: { ...turn("t1", "queued"), idempotency_key: input.idempotency_key },
          user_message: message("m1", { role: "author", text: input.text }),
        };
      },
    });
    await useChatStore.getState().bindProject("p1", apiSelection(service));
    useChatStore.getState().setDraft("讨论动机");
    await useChatStore.getState().send();
    const view = useChatStore.getState().views.c1!;
    // 结果未知：草稿保留、带上幂等键，明确提示“可能已发出”。
    expect(view.pending).toMatchObject({ text: "讨论动机" });
    expect(useChatStore.getState().drafts.c1).toBe("讨论动机");
    expect(view.pending!.idempotency_key).toBe(keys[0]);
    fail = false;
    await useChatStore.getState().retryUnknownSend();
    // 同一幂等键重发：服务端若已接受则返回同一轮次，不会二次计费。
    expect(keys[1]).toBe(keys[0]);
    expect(useChatStore.getState().views.c1!.pending).toBeNull();
  });

  it("服务端明确拒绝（有错误码）时不进入“结果未知”，直接提示", async () => {
    const service = fakeService({
      listConversations: async () => [
        { conversation_id: "c1", project_id: "p1", title: "甲", created_at: "", updated_at: "" },
      ],
      sendTurn: async () => {
        const { V2Error } = await import("../api/v2");
        throw new V2Error(
          {
            code: "CHAT_CONVERSATION_BUSY",
            category: "violation",
            where: "chat.turn",
            why: "会话忙",
            fix: "先停止当前回复",
            details: {},
          },
          409,
        );
      },
    });
    await useChatStore.getState().bindProject("p1", apiSelection(service));
    useChatStore.getState().setDraft("再来一条");
    await useChatStore.getState().send();
    const view = useChatStore.getState().views.c1!;
    expect(view.pending).toBeNull();
    expect(view.error).toContain("会话忙");
    expect(useChatStore.getState().drafts.c1).toBe("再来一条");
  });
});

describe("接着讨论（V03-1.5）", () => {
  const authorMessage = (id: string, text: string): ChatMessage => ({
    message_id: id,
    conversation_id: "c1",
    turn_id: null,
    role: "author",
    kind: "text",
    text,
    refs: [],
    model: null,
    status: "completed",
    error: null,
    created_at: "2026-10-17T00:00:00Z",
  });

  async function bindWithMessages(service: ChatService) {
    await useChatStore.getState().bindProject("p1", apiSelection(service));
    await useChatStore.getState().selectConversation("c1");
  }

  it("成功：主消息 reparent 进新轮次（同 message_id 不复制），轮次激活", async () => {
    const calls: string[][] = [];
    const service = fakeService({
      loadMessages: async () => [authorMessage("m1", "两姐妹在山中旅"), authorMessage("m2", "旅馆背景")],
      continueTurn: async (_p, _c, input) => {
        calls.push(input.source_message_ids);
        return {
          turn: turn("t9", "queued"),
          user_message: {
            ...authorMessage(input.source_message_ids[0]!, "两姐妹在山中旅"),
            turn_id: "t9",
          },
        };
      },
    });
    await bindWithMessages(service);
    const ok = await useChatStore.getState().continueFromMessages("c1", ["m1"]);
    expect(ok).toBe(true);
    const view = useChatStore.getState().views.c1!;
    // 引用不复制：m1 仍只有一条，turn_id 指向新轮次。
    const primaries = view.messages.filter((m) => m.message_id === "m1");
    expect(primaries).toHaveLength(1);
    expect(primaries[0]!.turn_id).toBe("t9");
    // 其余消息保持原位。
    expect(view.messages.find((m) => m.message_id === "m2")?.turn_id).toBeNull();
    expect(view.turns.t9).toBeDefined();
    expect(view.activeTurnId).toBe("t9");
    expect(calls).toEqual([["m1"]]);
  });

  it("已有回复进行中：拒绝继续并提示，不发出请求", async () => {
    const continueCalls: number[] = [];
    const service = fakeService({
      loadMessages: async () => [authorMessage("m1", "想法")],
      continueTurn: async () => {
        continueCalls.push(1);
        return { turn: turn("t9", "queued"), user_message: authorMessage("m1", "想法") };
      },
    });
    await bindWithMessages(service);
    useChatStore.setState((s) => ({
      views: {
        ...s.views,
        c1: { ...s.views.c1!, activeTurnId: "t-running" },
      },
    }));
    const ok = await useChatStore.getState().continueFromMessages("c1", ["m1"]);
    expect(ok).toBe(false);
    expect(continueCalls).toHaveLength(0);
    expect(useChatStore.getState().views.c1!.error).toContain("尚未结束");
  });

  it("网络故障：结果未知并记录引用范围，重试用同一幂等键", async () => {
    const keys: string[] = [];
    let fail = true;
    const service = fakeService({
      loadMessages: async () => [authorMessage("m1", "想法")],
      continueTurn: async (_p, _c, input) => {
        keys.push(input.idempotency_key);
        if (fail) throw new TypeError("Failed to fetch");
        return { turn: turn("t9", "queued"), user_message: authorMessage("m1", "想法") };
      },
    });
    await bindWithMessages(service);
    const ok = await useChatStore.getState().continueFromMessages("c1", ["m1"]);
    expect(ok).toBe(false);
    const view = useChatStore.getState().views.c1!;
    expect(view.pending).not.toBeNull();
    expect(view.pending!.sourceMessageIds).toEqual(["m1"]);
    expect(view.pending!.idempotency_key).toBe(keys[0]);
    fail = false;
    await useChatStore.getState().retryUnknownSend();
    // 同一请求重发：服务端幂等，不重复调用模型。
    expect(keys[1]).toBe(keys[0]);
    expect(useChatStore.getState().views.c1!.pending).toBeNull();
  });

  it("服务端明确拒绝（跨讨论/非作者消息）时不进入结果未知，直接提示", async () => {
    const service = fakeService({
      loadMessages: async () => [authorMessage("m1", "想法")],
      continueTurn: async () => {
        const { V2Error } = await import("../api/v2");
        throw new V2Error(
          {
            code: "CHAT_MESSAGE_NOT_FOUND",
            category: "violation",
            where: "chat.turn.send_from_messages",
            why: "引用的消息不存在、属于其他讨论或不是作者消息",
            fix: "只能引用本讨论内保存的想法继续",
            details: {},
          },
          404,
        );
      },
    });
    await bindWithMessages(service);
    const ok = await useChatStore.getState().continueFromMessages("c1", ["m-x"]);
    expect(ok).toBe(false);
    const view = useChatStore.getState().views.c1!;
    expect(view.pending).toBeNull();
    expect(view.error).toContain("不是作者消息");
  });
});

describe("服务选择与能力探测", () => {
  it("缺少新版端点 → 不支持并给重启指引（不返回空服务假装成功）", async () => {
    const selection = await selectChatService("p1", {
      mock: false,
      probe: async () => ({
        supported: false,
        protocol: null,
        reason: "missing_endpoints",
        detail: "前后端版本不匹配：请重启后端服务，再刷新页面。",
      }),
    });
    expect(selection.service).toBeNull();
    expect(selection.kind).toBe("unsupported");
    expect(selection.detail).toContain("重启后端");
  });

  it("端点可用 → 真实 API 服务；模拟入口 → 模拟服务并标注 simulated", async () => {
    const api = await selectChatService("p1", {
      mock: false,
      probe: async () => ({
        supported: true,
        protocol: "chat.v1",
        reason: "ok",
        detail: "",
      }),
      createApi: () => fakeService(),
    });
    expect(api.kind).toBe("api");
    expect(api.protocol).toBe("chat.v1");
    const sim = await selectChatService("p1", { mock: true });
    expect(sim.kind).toBe("simulator");
    expect(sim.service!.capabilities().simulated).toBe(true);
  });
});

describe("断线重连补偿", () => {
  it("重连成功后重新拉取消息，以服务端为准合并", async () => {
    let serverMessages: ChatMessage[] = [
      message("m1", { status: "completed", text: "服务端版本" }),
    ];
    let statusHandler: ((s: "connecting" | "open" | "closed", d?: string) => void) | null =
      null;
    const service = fakeService({
      listConversations: async () => [
        { conversation_id: "c1", project_id: "p1", title: "甲", created_at: "", updated_at: "" },
      ],
      loadMessages: async () => serverMessages,
      subscribe: (_p, _c, _l, onStatus) => {
        statusHandler = onStatus ?? null;
        return () => undefined;
      },
    });
    await useChatStore.getState().bindProject("p1", apiSelection(service));
    expect(useChatStore.getState().views.c1!.messages[0]!.text).toBe("服务端版本");
    // 断线：只改连接状态，不把模型执行标记成失败。
    statusHandler!("closed", "网络中断");
    let view = useChatStore.getState().views.c1!;
    expect(view.connection).toBe("closed");
    expect(view.messages[0]!.status).toBe("completed");
    // 重连后服务端有了新内容：以服务端为准补齐。
    serverMessages = [
      ...serverMessages,
      message("m2", { status: "completed", text: "断线期间的新回复", conversation_id: "c1" }),
    ];
    statusHandler!("open");
    await new Promise((r) => setTimeout(r, 20));
    view = useChatStore.getState().views.c1!;
    expect(view.connection).toBe("open");
    expect(view.messages.map((m) => m.message_id)).toEqual(["m1", "m2"]);
  });
});

describe("任务类型契约", () => {
  it("四类创作入口都有中文标签（界面文案不拼字符串）", () => {
    const kinds: TaskKind[] = ["plan", "chapter_draft", "check", "targeted_revision"];
    expect(kinds).toHaveLength(4);
  });
});
