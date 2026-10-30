/**
 * 会话状态（0.4 V04-10）。
 *
 * 关注四件容易写错的事：新对话不落盘、首次发送才建会话、切会话不串事件与草稿、
 * 旧的异步响应不能覆盖后选的会话。API 模块整体替身，状态逻辑用真实代码。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listConversations: vi.fn(),
  fetchConversationUi: vi.fn(),
  patchConversationUi: vi.fn(),
  createConversationWithText: vi.fn(),
  conversationMessages: vi.fn(),
  startTurn: vi.fn(),
  renameConversation: vi.fn(),
  streamEvents: vi.fn(),
}));

const ops = vi.hoisted(() => ({
  readOperation: vi.fn(),
  pauseOperation: vi.fn(),
  continueOperation: vi.fn(),
  interjectOperation: vi.fn(),
  operationKey: vi.fn(() => "key-1"),
}));

vi.mock("../api/operations", async () => {
  const actual = await vi.importActual<typeof import("../api/operations")>("../api/operations");
  return { ...actual, ...ops };
});

vi.mock("../api/workspace", async () => {
  const actual = await vi.importActual<typeof import("../api/workspace")>("../api/workspace");
  return { ...actual, ...api };
});

import { useStoryStore } from "./story";
import { useConversationsStore } from "./conversations";
import { EMPTY_CONTEXT, WorkspaceApiError } from "../api/workspace";
import type { TextReference } from "./textReferences";

const CONVERSATION = {
  id: "c1",
  title: "写第一章",
  created_at: "2026-10-21T10:00:00+08:00",
  updated_at: "2026-10-21T10:00:00+08:00",
};

function reset(): void {
  useConversationsStore.setState(useConversationsStore.getInitialState(), true);
}

/** 让挂起的 await 链推进一轮：事件流是长连接，不能 await 到底。 */
async function flush(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

function list(items = [CONVERSATION], recent: string | null = "c1") {
  return { items, recent_id: recent, active_turn: null, revision: "r1" };
}

const FINISHED = {
  seq: 3,
  at: "",
  type: "run_finished",
  status: "completed",
  recording: "committed",
  changed_paths: ["正文/0001 雨夜.md"],
  commit: "abc1234",
} as const;

beforeEach(() => {
  reset();
  useStoryStore.getState().reset();
  vi.clearAllMocks();
  api.listConversations.mockResolvedValue(list());
  api.fetchConversationUi.mockResolvedValue({ recent_id: "c1", drafts: {}, revision: "r1" });
  api.patchConversationUi.mockResolvedValue({ recent_id: "c1", drafts: {}, revision: "r2" });
  api.conversationMessages.mockResolvedValue({
    events: [{ seq: 1, at: "", type: "turn_started", text: "写第一章" }],
    last_seq: 1,
    active_turn: null,
    revision: "r1",
  });
  api.startTurn.mockResolvedValue({
    turn_id: "t1",
    conversation_id: "c1",
    started_at: "2026-10-21T10:00:00+08:00",
    operation_id: "op_1",
    generation: 1,
  });
  ops.readOperation.mockResolvedValue({
    id: "op_1",
    conversation_id: "c1",
    turn_id: "t1",
    purpose: "chat",
    target: "写作对话",
    status: "running",
    generation: 1,
    phase: "model",
    model: "",
    connection: "",
    note: "",
    error: null,
    pending_units: [],
    last_seq: 0,
    created_at: "",
    updated_at: "",
    capabilities: { pause: true, continue: false, interject: true },
  });
  ops.pauseOperation.mockResolvedValue({
    id: "op_1",
    conversation_id: "c1",
    turn_id: "t1",
    purpose: "chat",
    target: "写作对话",
    status: "paused",
    generation: 1,
    phase: "paused",
    model: "",
    connection: "",
    note: "",
    error: null,
    pending_units: [],
    last_seq: 0,
    created_at: "",
    updated_at: "",
    capabilities: { pause: false, continue: true, interject: false },
  });
  api.streamEvents.mockImplementation(async function* () {
    yield { seq: 1, at: "", type: "turn_started", text: "写第一章" };
    yield { seq: 2, at: "", type: "assistant_message", text: "写好了。" };
    yield FINISHED;
  });
});

describe("加载与恢复", () => {
  it("恢复上次看的正式会话，并读回它的事件", async () => {
    await useConversationsStore.getState().load("w1");

    const state = useConversationsStore.getState();
    expect(state.workspaceId).toBe("w1");
    expect(state.selectedId).toBe("c1");
    expect(state.entries.c1?.lastSeq).toBe(1);
    expect(state.entries.c1?.turns[0]?.prompt).toBe("写第一章");
    expect(api.conversationMessages).toHaveBeenCalledWith("w1", "c1", 0);
  });

  it("服务器上还有一轮在跑时，恢复运行状态", async () => {
    const active = {
      conversation_id: "c1",
      turn_id: "t9",
      state: "running" as const,
      model: "m",
      connection: "我的连接",
      started_at: "",
    };
    api.listConversations.mockResolvedValue({ ...list(), active_turn: active });
    api.conversationMessages.mockResolvedValue({
      events: [],
      last_seq: 0,
      active_turn: active,
      revision: "r1",
    });
    api.streamEvents.mockImplementation(async function* () {
      await new Promise(() => undefined);
    });

    void useConversationsStore.getState().load("w1");
    await flush();

    expect(useConversationsStore.getState().entries.c1?.running).toBe(true);
    expect(useConversationsStore.getState().entries.c1?.activeTurn?.turn_id).toBe("t9");
  });

  it("没有会话时停在临时新状态，不自己建一个", async () => {
    api.listConversations.mockResolvedValue(list([], null));
    api.fetchConversationUi.mockResolvedValue({ recent_id: null, drafts: {}, revision: "0" });

    await useConversationsStore.getState().load("w2");

    expect(useConversationsStore.getState().selectedId).toBeNull();
    expect(api.createConversationWithText).not.toHaveBeenCalled();
    expect(useConversationsStore.getState().items).toEqual([]);
  });
});

describe("新对话与首次发送", () => {
  it("A 到 B 再到 A 后，第一次 A 的列表不能覆盖新列表", async () => {
    let resolve!: (value: ReturnType<typeof list>) => void;
    api.listConversations.mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const old = useConversationsStore.getState().load("w1");
    await useConversationsStore.getState().load("w2");
    api.listConversations.mockResolvedValueOnce(list([], null));
    await useConversationsStore.getState().load("w1");
    resolve(list()); await old;
    expect(useConversationsStore.getState().workspaceId).toBe("w1");
    expect(useConversationsStore.getState().items).toEqual([]);
    expect(useConversationsStore.getState().selectedId).toBeNull();
  });

  it("点新对话只切到临时状态，不请求后端", async () => {
    await useConversationsStore.getState().load("w1");

    useConversationsStore.getState().newConversation();

    expect(useConversationsStore.getState().selectedId).toBeNull();
    expect(api.createConversationWithText).not.toHaveBeenCalled();
    expect(api.startTurn).not.toHaveBeenCalled();
  });

  it("首次发送一次请求建会话并受理首轮", async () => {
    await useConversationsStore.getState().load("w1");
    useConversationsStore.getState().newConversation();
    useConversationsStore.getState().setDraft("写第一章");
    api.createConversationWithText.mockResolvedValue({
      conversation_id: "c2",
      turn_id: "t2",
      started_at: "",
      title: "写第一章",
      request_key: "ui-1",
    });
    // 结束时回读列表：新会话已经在里面，与真实后端一致。
    api.listConversations.mockResolvedValue(
      list([{ ...CONVERSATION, id: "c2", title: "写第一章" }, CONVERSATION]),
    );

    await useConversationsStore.getState().send("写第一章");

    expect(api.createConversationWithText).toHaveBeenCalledWith(
      "w1",
      expect.objectContaining({ text: "写第一章" }),
    );
    expect(api.startTurn).not.toHaveBeenCalled();
    const state = useConversationsStore.getState();
    expect(state.selectedId).toBe("c2");
    expect(state.items.map((item) => item.id)).toEqual(["c2", "c1"]);
    // 新草稿交给正式会话后不再留在 new 槽位。
    expect(state.drafts.new).toBeUndefined();
    expect(state.entries.c2?.turns[0]?.result?.status).toBe("completed");
    expect(state.entries.c2?.running).toBe(false);
  });

  it("已有会话发送走轮次接口，并保留上一轮的游标", async () => {
    await useConversationsStore.getState().load("w1");

    await useConversationsStore.getState().send("继续");

    expect(api.startTurn).toHaveBeenCalledWith(
      "w1",
      "c1",
      expect.objectContaining({ text: "继续" }),
    );
    expect(api.streamEvents).toHaveBeenCalledWith("w1", "c1", 1, expect.any(AbortSignal));
  });

  it("受理失败时保留草稿，也不多出会话", async () => {
    await useConversationsStore.getState().load("w1");
    useConversationsStore.getState().newConversation();
    useConversationsStore.getState().setDraft("写第一章");
    api.createConversationWithText.mockRejectedValue(new WorkspaceApiError("WORKSPACE_BUSY", "作品忙碌", "等待当前任务结束", 409, "对话受理"));

    await useConversationsStore.getState().send("写第一章");

    const state = useConversationsStore.getState();
    expect(state.drafts.new).toBe("写第一章");
    expect(state.items.map((item) => item.id)).toEqual(["c1"]);
    expect(state.error).toContain("作品忙碌");
  });

  it("受理期间新输入的文字不会被旧响应清掉", async () => {
    await useConversationsStore.getState().load("w1");
    let accept: (value: unknown) => void = () => undefined;
    api.startTurn.mockReturnValue(
      new Promise((resolve) => {
        accept = resolve;
      }),
    );

    const sending = useConversationsStore.getState().send("写第一章");
    // 作者在等待受理时又输入了新的要求。
    useConversationsStore.getState().setDraft("再补一句");
    accept({ turn_id: "t1", conversation_id: "c1", started_at: "" });
    await sending;

    expect(useConversationsStore.getState().drafts.c1).toBe("再补一句");
  });
});

describe("切换会话", () => {
  it("切换前保存旧草稿，切换后只显示新会话的事件", async () => {
    api.listConversations.mockResolvedValue(list([CONVERSATION, { ...CONVERSATION, id: "c2", title: "人物动机" }]));
    api.conversationMessages.mockImplementation(async (_w: string, id: string) => ({
      events: [
        { seq: 1, at: "", type: "turn_started", text: id === "c1" ? "写第一章" : "讨论动机" },
      ],
      last_seq: 1,
      active_turn: null,
      revision: "r1",
    }));
    await useConversationsStore.getState().load("w1");
    useConversationsStore.getState().setDraft("草稿 A");

    await useConversationsStore.getState().select("c2");

    const state = useConversationsStore.getState();
    expect(api.patchConversationUi).toHaveBeenCalledWith(
      "w1",
      expect.objectContaining({ set_draft: true, draft_target: "c1", draft_text: "草稿 A" }),
      expect.any(String),
    );
    expect(state.selectedId).toBe("c2");
    expect(state.entries.c2?.turns[0]?.prompt).toBe("讨论动机");
    expect(state.entries.c1?.turns[0]?.prompt).toBe("写第一章");
  });

  it("切走后旧请求返回不覆盖当前会话", async () => {
    // 启动时没有「上次看的会话」，两个会话都还没选中。
    api.listConversations.mockResolvedValue(
      list([CONVERSATION, { ...CONVERSATION, id: "c2" }], null),
    );
    api.fetchConversationUi.mockResolvedValue({ recent_id: null, drafts: {}, revision: "r1" });
    let resolveSlow: (value: unknown) => void = () => undefined;
    api.conversationMessages.mockImplementation((_w: string, id: string) => {
      if (id === "c1") {
        return new Promise((resolve) => {
          resolveSlow = resolve;
        });
      }
      return Promise.resolve({
        events: [{ seq: 1, at: "", type: "turn_started", text: "后选的会话" }],
        last_seq: 1,
        active_turn: null,
        revision: "r1",
      });
    });
    // 启动恢复还在读 c1 时，作者自己切到了 c2。
    const loading = useConversationsStore.getState().load("w1");
    await flush();
    await useConversationsStore.getState().select("c2");
    resolveSlow({
      events: [{ seq: 1, at: "", type: "turn_started", text: "迟到的响应" }],
      last_seq: 1,
      active_turn: null,
      revision: "r1",
    });
    await loading;

    const state = useConversationsStore.getState();
    expect(state.selectedId).toBe("c2");
    expect(state.entries.c2?.turns[0]?.prompt).toBe("后选的会话");
    // 迟到的响应只更新它自己的会话，不抢回视图。
    expect(state.entries.c1?.turns[0]?.prompt).toBe("迟到的响应");
  });

  it("切走不暂停服务器任务：原会话仍在运行", async () => {
    api.listConversations.mockResolvedValue(list([CONVERSATION, { ...CONVERSATION, id: "c2" }]));
    api.streamEvents.mockImplementation(async function* () {
      yield { seq: 1, at: "", type: "turn_started", text: "写第一章" };
      await new Promise(() => undefined);
    });
    await useConversationsStore.getState().load("w1");
    void useConversationsStore.getState().send("写第一章");
    await flush();

    await useConversationsStore.getState().select("c2");

    expect(useConversationsStore.getState().entries.c1?.running).toBe(true);
    expect(ops.pauseOperation).not.toHaveBeenCalled();
  });

  it("暂停立即标为请求中，仍等待终态，并避免重复提交", async () => {
    api.streamEvents.mockImplementation(async function* () {
      yield { seq: 1, at: "", type: "turn_started", text: "写第一章" };
      await new Promise(() => undefined);
    });
    await useConversationsStore.getState().load("w1");
    void useConversationsStore.getState().send("写第一章");
    await flush();

    let resolve: (value: unknown) => void = () => undefined;
    ops.pauseOperation.mockImplementation(
      () => new Promise((done) => { resolve = done; }),
    );
    const pending = useConversationsStore.getState().pause();

    expect(useConversationsStore.getState().entries.c1?.activeTurn?.state).toBe("stop_requested");
    await useConversationsStore.getState().pause();
    expect(ops.pauseOperation).toHaveBeenCalledTimes(1);
    resolve({
      id: "op_1",
      conversation_id: "c1",
      turn_id: "t1",
      purpose: "chat",
      target: "写作对话",
      status: "paused",
      generation: 1,
      phase: "paused",
      model: "",
      connection: "",
      note: "",
      error: null,
      pending_units: [],
      last_seq: 0,
      created_at: "",
      updated_at: "",
      capabilities: { pause: false, continue: true, interject: false },
    });
    await pending;

    expect(ops.pauseOperation).toHaveBeenCalledWith("op_1", "key-1");
    expect(useConversationsStore.getState().entries.c1?.running).toBe(true);
  });

  it("停止请求失败时保留运行状态并恢复重试入口", async () => {
    api.streamEvents.mockImplementation(async function* () {
      yield { seq: 1, at: "", type: "turn_started", text: "写第一章" };
      await new Promise(() => undefined);
    });
    await useConversationsStore.getState().load("w1");
    void useConversationsStore.getState().send("写第一章");
    await flush();
    ops.pauseOperation.mockRejectedValue(new Error("network"));

    await useConversationsStore.getState().pause();

    expect(useConversationsStore.getState().entries.c1?.activeTurn?.state).not.toBe("stop_requested");
    expect(useConversationsStore.getState().entries.c1?.running).toBe(true);
    expect(useConversationsStore.getState().error).toContain("[REQUEST_FAILED]");
  });
});

describe("缺批回读与实时流", () => {
  it("回读期间收到的事件不会被旧快照覆盖，游标不回退", async () => {
    api.conversationMessages.mockResolvedValueOnce({
      events: [{ seq: 1, at: "", type: "turn_started", turn_id: "t1", text: "写第一章" }],
      last_seq: 1,
      active_turn: null,
      revision: "r1",
    });
    await useConversationsStore.getState().load("w1");
    let release: (value: unknown) => void = () => undefined;
    api.conversationMessages.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    api.streamEvents.mockImplementation(async function* () {
      yield { seq: 2, at: "", type: "assistant_started", turn_id: "t2", message_id: "m1" };
      yield {
        seq: 3,
        at: "",
        type: "assistant_block_started",
        turn_id: "t2",
        message_id: "m1",
        block_id: "b1",
        kind: "text",
        order: 0,
      };
      yield {
        seq: 4,
        at: "",
        type: "assistant_block_delta",
        turn_id: "t2",
        message_id: "m1",
        block_id: "b1",
        text: "雪停",
        part: 0,
      };
      // 缺了 part 1：界面回读日志补齐，但实时流仍在继续。
      yield {
        seq: 8,
        at: "",
        type: "assistant_block_delta",
        turn_id: "t2",
        message_id: "m1",
        block_id: "b1",
        text: "了。",
        part: 2,
      };
      yield {
        seq: 9,
        at: "",
        type: "assistant_message",
        turn_id: "t2",
        message_id: "m1",
        text: "雪停了。",
        completion: "completed",
      };
      yield {
        seq: 10,
        at: "",
        type: "run_finished",
        turn_id: "t2",
        status: "completed",
        recording: "no_change",
        changed_paths: [],
        commit: null,
      };
    });

    void useConversationsStore.getState().send("继续");
    await flush();
    expect(useConversationsStore.getState().entries.c1?.lastSeq).toBe(10);

    // 回读请求发出时日志只到 6；之后实时流又收到了 9、10。
    release({
      events: [
        { seq: 1, at: "", type: "turn_started", turn_id: "t1", text: "写第一章" },
        { seq: 2, at: "", type: "assistant_started", turn_id: "t2", message_id: "m1" },
        {
          seq: 3,
          at: "",
          type: "assistant_block_started",
          turn_id: "t2",
          message_id: "m1",
          block_id: "b1",
          kind: "text",
          order: 0,
        },
        {
          seq: 4,
          at: "",
          type: "assistant_block_delta",
          turn_id: "t2",
          message_id: "m1",
          block_id: "b1",
          text: "雪停",
          part: 0,
        },
        {
          seq: 5,
          at: "",
          type: "assistant_block_delta",
          turn_id: "t2",
          message_id: "m1",
          block_id: "b1",
          text: "了",
          part: 1,
        },
        {
          seq: 6,
          at: "",
          type: "assistant_block_finished",
          turn_id: "t2",
          message_id: "m1",
          block_id: "b1",
          kind: "text",
          order: 0,
          text: "雪停了",
          completion: "completed",
          duration_ms: 10,
        },
      ],
      last_seq: 6,
      active_turn: null,
      revision: "r1",
    });
    await flush();

    const entry = useConversationsStore.getState().entries.c1;
    expect(entry?.lastSeq).toBe(10);
    expect(entry?.needsReload).toBe(false);
    expect(entry?.turns.find((turn) => turn.id === "t2")?.result?.status).toBe("completed");
  });
});

describe("草稿与列表", () => {
  it("草稿按版本保存，成功后采用新版本号", async () => {
    await useConversationsStore.getState().load("w1");
    useConversationsStore.getState().setDraft("先写开头");

    await useConversationsStore.getState().saveDraft();

    expect(api.patchConversationUi).toHaveBeenLastCalledWith(
      "w1",
      expect.objectContaining({ set_draft: true, draft_target: "c1", draft_text: "先写开头" }),
      expect.any(String),
    );
    expect(useConversationsStore.getState().revision).toBe("r2");
  });

  it("版本号过期时自动用新版本号重发一次，不要求作者重新输入", async () => {
    await useConversationsStore.getState().load("w1");
    useConversationsStore.getState().setDraft("我的草稿");
    api.patchConversationUi
      .mockRejectedValueOnce(
        new WorkspaceApiError("CONVERSATION_UI_CONFLICT", "已被其他窗口改动", "刷新后再试", 409),
      )
      .mockResolvedValueOnce({ recent_id: "c1", drafts: {}, revision: "r9" });

    await useConversationsStore.getState().saveDraft();

    const state = useConversationsStore.getState();
    // 重发用的是重新读到的版本号（mock 返回 r1），而不是第一次的旧值。
    expect(api.patchConversationUi).toHaveBeenLastCalledWith(
      "w1",
      expect.objectContaining({ set_draft: true, draft_target: "c1", draft_text: "我的草稿" }),
      "r1",
    );
    expect(state.drafts.c1).toBe("我的草稿");
    expect(state.error).toBeNull();
    expect(state.revision).toBe("r9");
  });

  it("重发仍冲突时保留本地文字并提示，不静默覆盖", async () => {
    await useConversationsStore.getState().load("w1");
    useConversationsStore.getState().setDraft("我的草稿");
    api.patchConversationUi.mockRejectedValue(
      new WorkspaceApiError("CONVERSATION_UI_CONFLICT", "已被其他窗口改动", "刷新后再试", 409),
    );

    await useConversationsStore.getState().saveDraft();

    const state = useConversationsStore.getState();
    expect(state.drafts.c1).toBe("我的草稿");
    expect(state.error).toContain("保留");
  });

  it("连续写入串行执行，后一条使用前一条返回的版本号", async () => {
    await useConversationsStore.getState().load("w1");
    useConversationsStore.getState().setDraft("先写开头");
    let releaseFirst: (ui: { recent_id: string | null; drafts: Record<string, string>; revision: string }) => void = () => {};
    api.patchConversationUi
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            releaseFirst = resolve;
          }),
      )
      .mockResolvedValueOnce({ recent_id: "c1", drafts: {}, revision: "r3" });

    const first = useConversationsStore.getState().saveDraft();
    const second = useConversationsStore.getState().saveDraft();
    await flush();
    // 第一条还在途中，第二条草稿写入不应已经发出（「记住最近会话」是另一类写入）。
    const draftWrites = () =>
      api.patchConversationUi.mock.calls.filter(
        ([, body]) => (body as { set_draft?: boolean }).set_draft === true,
      );
    expect(draftWrites()).toHaveLength(1);

    releaseFirst({ recent_id: "c1", drafts: {}, revision: "r2" });
    await first;
    await second;

    expect(draftWrites()).toHaveLength(2);
    // 后一条用的是前一条返回的版本号，而不是两次都拿 r1。
    expect(draftWrites().at(-1)?.[2]).toBe("r2");
  });

  it("保存失败提示在下次保存成功后退场，不再一直挂着", async () => {
    await useConversationsStore.getState().load("w1");
    useConversationsStore.getState().setDraft("先写开头");
    api.patchConversationUi.mockRejectedValueOnce(
      new WorkspaceApiError("HTTP_500", "请求没有成功（HTTP 500）", "稍后重试", 500),
    );
    await useConversationsStore.getState().saveDraft();
    expect(useConversationsStore.getState().error).toContain("草稿还没保存");

    await useConversationsStore.getState().saveDraft();

    const state = useConversationsStore.getState();
    expect(state.error).toBeNull();
    expect(state.revision).toBe("r2");
  });

  it("改名更新列表里的标题", async () => {
    await useConversationsStore.getState().load("w1");
    api.renameConversation.mockResolvedValue({ ...CONVERSATION, title: "人物动机" });

    await useConversationsStore.getState().rename("c1", " 人物动机 ");

    expect(api.renameConversation).toHaveBeenCalledWith("w1", "c1", "人物动机");
    expect(useConversationsStore.getState().items[0]?.title).toBe("人物动机");
  });

  it("草稿的参考与文字同一次保存，删除文字时一并删除", async () => {
    await useConversationsStore.getState().load("w1");
    useConversationsStore.getState().setDraft("参考第一章改第三章");
    useConversationsStore.getState().setDraftContext("正文/0001 雨夜.md", ["正文/0001 雨夜.md"]);

    await useConversationsStore.getState().saveDraft();

    expect(api.patchConversationUi).toHaveBeenLastCalledWith(
      "w1",
      expect.objectContaining({
        set_draft: true,
        draft_text: "参考第一章改第三章",
        set_draft_context: true,
        draft_selected_path: "正文/0001 雨夜.md",
        draft_reference_paths: ["正文/0001 雨夜.md"],
      }),
      expect.any(String),
    );

    // 只选文件、没有文字时不创建会话：草稿槽位仍然是 new。
    expect(useConversationsStore.getState().selectedId).toBe("c1");
    expect(api.createConversationWithText).not.toHaveBeenCalled();
  });

  it("发送时把当前参考与版本一起交给受理，并在受理后清掉草稿参考", async () => {
    await useConversationsStore.getState().load("w1");
    api.createConversationWithText.mockResolvedValue({
      conversation_id: "c2",
      turn_id: "t2",
      started_at: "2026-10-22T10:00:00+08:00",
      title: "参考第一章",
      request_key: "k1",
    });
    api.conversationMessages.mockResolvedValue({ events: [], last_seq: 0, active_turn: null });
    api.streamEvents.mockImplementation(async function* () {
      yield { ...FINISHED, seq: 1 };
    });
    useConversationsStore.getState().newConversation();
    useConversationsStore.getState().setDraft("参考第一章改第三章");
    useConversationsStore.getState().setDraftContext("正文/0001 雨夜.md", ["正文/0001 雨夜.md"]);
    useConversationsStore.getState().addIdeaReference({ ideaId: "idea_1", revisionId: "irev_1", preview: "旧识", chars: 2 });

    await useConversationsStore.getState().send("参考第一章改第三章", {
      selected_path: "正文/0001 雨夜.md",
      reference_paths: ["正文/0001 雨夜.md"],
      observed_digests: { "正文/0001 雨夜.md": "d1" },
    });

    expect(api.createConversationWithText).toHaveBeenCalledWith(
      "w1",
      expect.objectContaining({
        text: "参考第一章改第三章",
        context: {
          selected_path: "正文/0001 雨夜.md",
          reference_paths: ["正文/0001 雨夜.md"],
          idea_references: [{ idea_id: "idea_1", revision_id: "irev_1" }],
          observed_digests: { "正文/0001 雨夜.md": "d1" },
        },
      }),
    );
    expect(useConversationsStore.getState().draftContexts.new).toBeUndefined();
    expect(useConversationsStore.getState().draftIdeas.new).toBeUndefined();
    expect(useConversationsStore.getState().drafts.new).toBeUndefined();
  });

  it("目录里加入对话只追加参考，不重复也不打乱顺序", async () => {
    await useConversationsStore.getState().load("w1");
    useConversationsStore.getState().newConversation();

    useConversationsStore.getState().addReference("正文/0002 别章.md");
    useConversationsStore.getState().addReference("正文/0001 雨夜.md");
    useConversationsStore.getState().addReference("正文/0002 别章.md");

    expect(useConversationsStore.getState().draftContexts.new?.reference_paths).toEqual([
      "正文/0002 别章.md",
      "正文/0001 雨夜.md",
    ]);
  });

  it("发送后不清掉受理期间新选的参考", async () => {
    await useConversationsStore.getState().load("w1");
    let resolveCreate: (value: unknown) => void = () => undefined;
    api.createConversationWithText.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveCreate = resolve;
        }),
    );
    api.conversationMessages.mockResolvedValue({ events: [], last_seq: 0, active_turn: null });
    api.streamEvents.mockImplementation(async function* () {
      yield { ...FINISHED, seq: 1 };
    });
    useConversationsStore.getState().newConversation();
    useConversationsStore.getState().setDraft("参考第一章改第三章");
    useConversationsStore.getState().setDraftContext(null, ["正文/0001 雨夜.md"]);

    const sending = useConversationsStore.getState().send("参考第一章改第三章", {
      selected_path: null,
      reference_paths: ["正文/0001 雨夜.md"],
      observed_digests: {},
    });
    // 受理还没返回：作者在目录里又加了一份参考。
    useConversationsStore.getState().addReference("正文/0002 别章.md");
    resolveCreate({
      conversation_id: "c5",
      turn_id: "t5",
      started_at: "2026-10-22T13:00:00+08:00",
      title: "参考第一章改第三章",
      request_key: "k5",
    });
    await sending;

    // 文字随这一轮交出去，新选的参考留在草稿里等下一次发送。
    expect(useConversationsStore.getState().drafts.new).toBeUndefined();
    expect(useConversationsStore.getState().draftContexts.new?.reference_paths).toEqual([
      "正文/0001 雨夜.md",
      "正文/0002 别章.md",
    ]);
  });

  it("检查任务把目标文件一起提交", async () => {
    await useConversationsStore.getState().load("w1");
    api.createConversationWithText.mockResolvedValue({
      conversation_id: "c4",
      turn_id: "t4",
      started_at: "2026-10-22T12:00:00+08:00",
      title: "检查本次改动",
      request_key: "k3",
    });
    api.conversationMessages.mockResolvedValue({ events: [], last_seq: 0, active_turn: null });
    api.streamEvents.mockImplementation(async function* () {
      yield { ...FINISHED, seq: 1 };
    });
    useConversationsStore.getState().newConversation();

    await useConversationsStore.getState().send("检查本次改动：第三章", undefined, {
      purpose: "review",
      reviewPaths: ["正文/0003 书房.md"],
    });

    expect(api.createConversationWithText).toHaveBeenCalledWith(
      "w1",
      expect.objectContaining({
        purpose: "review",
        review_paths: ["正文/0003 书房.md"],
      }),
    );
  });

});

describe("灵感引用（I3）", () => {
  it("同一条目重复加入同修订忽略，换修订原位更新，不叠出第二个引用", () => {
    reset();
    const store = useConversationsStore.getState();
    store.addIdeaReference({ ideaId: "idea_1", revisionId: "irev_1", preview: "第一版", chars: 3 });
    store.addIdeaReference({ ideaId: "idea_1", revisionId: "irev_1", preview: "第一版", chars: 3 });
    expect(useConversationsStore.getState().draftIdeas.new).toHaveLength(1);

    store.addIdeaReference({ ideaId: "idea_2", revisionId: "irev_2", preview: "第二条", chars: 3 });
    store.updateIdeaReference({ ideaId: "idea_1", revisionId: "irev_9", preview: "第三版", chars: 3 });

    const items = useConversationsStore.getState().draftIdeas.new ?? [];
    expect(items.map((item) => item.idea_id)).toEqual(["idea_1", "idea_2"]);
    expect(items[0]).toMatchObject({ revision_id: "irev_9", preview: "第三版" });

    store.removeIdeaReference("idea_1");
    expect(useConversationsStore.getState().draftIdeas.new?.map((item) => item.idea_id)).toEqual([
      "idea_2",
    ]);
  });

  it("发送失败保留引用，受理成功后清掉本轮的引用", async () => {
    reset();
    useConversationsStore.setState({ workspaceId: "w1" });
    const store = useConversationsStore.getState();
    store.addIdeaReference({ ideaId: "idea_1", revisionId: "irev_1", preview: "第一版", chars: 3 });
    store.setDraft("用这条灵感");
    api.createConversationWithText.mockRejectedValueOnce(new Error("网络中断"));
    api.conversationMessages.mockResolvedValue({ events: [], last_seq: 0, active_turn: null });
    api.streamEvents.mockImplementation(async function* () {
      yield { ...FINISHED, seq: 1 };
    });

    await useConversationsStore.getState().send("用这条灵感", {
      selected_path: null,
      reference_paths: [],
      observed_digests: {},
    });
    expect(useConversationsStore.getState().draftIdeas.new).toHaveLength(1);

    api.createConversationWithText.mockResolvedValue({
      conversation_id: "c9",
      turn_id: "t9",
      started_at: "2026-10-22T10:00:00+08:00",
      title: "用这条灵感",
      request_key: "k9",
    });
    await useConversationsStore.getState().send("用这条灵感", {
      selected_path: null,
      reference_paths: [],
      observed_digests: {},
    });
    await flush();
    expect(useConversationsStore.getState().draftIdeas.new ?? []).toHaveLength(0);
  });
});

describe("正文与装载模块的选段引用", () => {
  const quote: TextReference = { id: "quote-1", text: "原文第一行。\n原文第二行。", title: "雨夜", source: "正文/0001 雨夜.md", version: "digest-1" };
  const moduleQuote: TextReference = { id: "quote-2", text: "钟慢了七分钟。", title: "港口线索", source: "module:library/module (binding:binding-1)", version: "rev-1" };

  it("添加去重、切会话隔离，修改文件参考不丢选段，草稿保存包含原文", async () => {
    await useConversationsStore.getState().load("w1");
    const store = useConversationsStore.getState();
    expect(store.addTextReference(quote)).toBe(true);
    expect(store.addTextReference({ ...quote, id: "duplicate" })).toBe(true);
    store.setDraftContext(null, ["正文/0002 来信.md"]);
    await store.saveDraft();
    expect(useConversationsStore.getState().draftContexts.c1.text_references).toEqual([quote]);
    expect(api.patchConversationUi.mock.calls.at(-1)?.[1].draft_text_references).toEqual([quote]);
    store.newConversation();
    expect(useConversationsStore.getState().draftContexts.new?.text_references ?? []).toEqual([]);
    store.addTextReference(moduleQuote);
    store.removeTextReference(moduleQuote.id);
    expect(useConversationsStore.getState().draftContexts.new?.text_references).toEqual([]);
    expect(useConversationsStore.getState().draftContexts.c1.text_references).toEqual([quote]);
  });

  it("受理失败保留引用，成功后原文与来源进入历史请求并清除已提交引用", async () => {
    await useConversationsStore.getState().load("w1");
    const store = useConversationsStore.getState();
    store.setDraft("请核对这两段");
    store.addTextReference(quote); store.addTextReference(moduleQuote);
    api.startTurn.mockRejectedValueOnce(new Error("断线"));
    await store.send("请核对这两段");
    expect(useConversationsStore.getState().draftContexts.c1.text_references).toHaveLength(2);
    await store.send("请核对这两段");
    const request = api.startTurn.mock.calls.at(-1)?.[2];
    expect(request.text).toContain("请核对这两段");
    expect(request.text).toContain("> 原文第一行。\n> 原文第二行。");
    expect(request.text).toContain(moduleQuote.source);
    expect(request.text).toContain("版本 rev-1");
    expect(request.text).toContain("> 钟慢了七分钟。");
    expect(useConversationsStore.getState().draftContexts.c1).toBeUndefined();
  });

  it("受理期间新增选段不会被旧响应清除", async () => {
    await useConversationsStore.getState().load("w1");
    const store = useConversationsStore.getState();
    store.setDraft("核对"); store.addTextReference(quote);
    let accept!: (value: { turn_id: string; conversation_id: string; started_at: string }) => void;
    api.startTurn.mockImplementationOnce(() => new Promise(resolve => { accept = resolve; }));
    const sending = store.send("核对");
    store.addTextReference(moduleQuote);
    accept({ turn_id: "t2", conversation_id: "c1", started_at: "" });
    await sending;
    expect(api.startTurn.mock.calls[0][2].text).not.toContain(moduleQuote.text);
    expect(useConversationsStore.getState().draftContexts.c1.text_references).toEqual([quote, moduleQuote]);
  });

  it("拒绝过长选段和第十一个选段，保留已有引用", async () => {
    await useConversationsStore.getState().load("w1");
    const store = useConversationsStore.getState();
    expect(store.addTextReference({ ...quote, text: "字".repeat(20_001) })).toBe(false);
    for (let i = 0; i < 10; i++) expect(store.addTextReference({ ...quote, id: `q${i}`, text: `选段${i}` })).toBe(true);
    expect(store.addTextReference(moduleQuote)).toBe(false);
    expect(useConversationsStore.getState().draftContexts.c1.text_references).toHaveLength(10);
  });
});


  it("线索引用只进草稿：同版本去重，新版本不静默替换", async () => {
    await useConversationsStore.getState().load("w1");
    const store = useConversationsStore.getState();
    const key = store.selectedId ?? "new";
    const reference = {
      workspace_lineage: "ws",
      clue_id: "clue_1",
      clue_revision: "rev_1",
      browse_as_of_chapter: 2,
    };
    expect(store.addClueReference(reference, "照片里的时间")).toBe(true);
    expect(useConversationsStore.getState().draftContexts[key]?.clue_references).toEqual([
      { ...reference, title: "照片里的时间" },
    ]);
    expect(useConversationsStore.getState().addClueReference(reference, "照片里的时间")).toBe(true);
    expect(useConversationsStore.getState().draftContexts[key]?.clue_references).toHaveLength(1);
    expect(
      useConversationsStore.getState().addClueReference({ ...reference, clue_revision: "rev_2" }, "照片里的时间"),
    ).toBe(false);
    expect(useConversationsStore.getState().draftContexts[key]?.clue_references?.[0].clue_revision).toBe("rev_1");
    expect(useConversationsStore.getState().error).toContain("已经有更新的版本");
    useConversationsStore.getState().removeClueReference("clue_1", "rev_1");
    expect(useConversationsStore.getState().draftContexts[key]?.clue_references).toEqual([]);
  });

  it("发送普通消息时带上草稿里的线索引用，明确用途不夹带", async () => {
    await useConversationsStore.getState().load("w1");
    useConversationsStore.getState().newConversation();
    const key = "new";
    const reference = {
      workspace_lineage: "ws",
      clue_id: "clue_1",
      clue_revision: "rev_1",
      browse_as_of_chapter: 2,
    };
    useConversationsStore.setState(state => ({
      draftContexts: { ...state.draftContexts, [key]: { ...(state.draftContexts[key] ?? { selected_path: null, reference_paths: [] }), clue_references: [reference] } },
    }));
    api.createConversationWithText.mockResolvedValue({
      conversation_id: "c6",
      turn_id: "t6",
      started_at: "2026-10-22T13:00:00+08:00",
      title: "写下一章",
      request_key: "k6",
    });
    api.conversationMessages.mockResolvedValue({ events: [], last_seq: 0, active_turn: null });
    api.streamEvents.mockImplementation(async function* () {
      yield { ...FINISHED, seq: 1 };
    });
    await useConversationsStore.getState().send("继续写这一章");
    const request = api.createConversationWithText.mock.calls[0][1] as {
      context: { clue_references?: unknown[] };
    };
    expect(request.context.clue_references).toEqual([reference]);

    api.createConversationWithText.mockClear();
    useConversationsStore.getState().newConversation();
    await useConversationsStore.getState().send("检查第三章", undefined, {
      purpose: "review",
      reviewPaths: ["正文/0003 书房.md"],
    });
    const review = api.createConversationWithText.mock.calls[0][1] as {
      context: { clue_references?: unknown[] };
    };
    expect(review.context.clue_references).toBeUndefined();
  });


it("构思面普通消息冻结当前备选和节点，浏览不会改到主方案", async () => {
  await useConversationsStore.getState().load("w1");
  useConversationsStore.getState().newConversation();
  useStoryStore.setState({ open: true, variantId: "alt", nodeId: "n2", graph: {
    variant_id: "alt", name: "备选", revision: "alt-r1", stages: [], nodes: [], edges: [], threads: [],
    event_times: [], constraints: [], collapsed: [],
  } });
  await useConversationsStore.getState().send("调整所选节点");
  expect(api.createConversationWithText.mock.calls[0][1].context.story).toEqual({
    variant_id: "alt", revision: "alt-r1", node_ids: ["n2"], purpose: "design",
  });
});

it("构思表单受理后即显示任务，不等待模型结束且保留普通草稿", async () => {
  await useConversationsStore.getState().load("w1");
  const text = "创建构思：\n湿手套的发现与对质";
  useConversationsStore.getState().setDraft(text);
  let finish: () => void = () => undefined;
  const gate = new Promise<void>(resolve => { finish = resolve; });
  api.streamEvents.mockImplementation(async function* () {
    await gate;
    yield FINISHED;
  });
  const accepted = vi.fn();
  const submitted = useConversationsStore.getState().send(text, {
    ...EMPTY_CONTEXT,
    story: { variant_id: "", revision: "", node_ids: [], purpose: "design" },
  }, { purpose: "chat", storyAction: "create", preserveDraft: true, onAccepted: accepted });
  await flush();
  expect(accepted).toHaveBeenCalledWith({ conversationId: "c1", turnId: "t1" });
  expect(api.startTurn.mock.calls[0][2].story_action).toBe("create");
  expect(useConversationsStore.getState().drafts.c1).toBe(text);
  finish();
  await submitted;
  expect(useConversationsStore.getState().drafts.c1).toBe(text);
});
