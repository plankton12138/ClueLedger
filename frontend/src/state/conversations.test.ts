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
  stopTurn: vi.fn(),
  renameConversation: vi.fn(),
  streamEvents: vi.fn(),
}));

vi.mock("../api/workspace", async () => {
  const actual = await vi.importActual<typeof import("../api/workspace")>("../api/workspace");
  return { ...actual, ...api };
});

import { useConversationsStore } from "./conversations";
import { WorkspaceApiError } from "../api/workspace";

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
  });
  api.stopTurn.mockResolvedValue({ stopped: true });
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

    expect(api.createConversationWithText).toHaveBeenCalledWith("w1", "写第一章", expect.any(String));
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

    expect(api.startTurn).toHaveBeenCalledWith("w1", "c1", "继续", expect.any(String));
    expect(api.streamEvents).toHaveBeenCalledWith("w1", "c1", 1, expect.any(AbortSignal));
  });

  it("受理失败时保留草稿，也不多出会话", async () => {
    await useConversationsStore.getState().load("w1");
    useConversationsStore.getState().newConversation();
    useConversationsStore.getState().setDraft("写第一章");
    api.createConversationWithText.mockRejectedValue(new Error("作品忙碌"));

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
      { set_draft: true, draft_target: "c1", draft_text: "草稿 A" },
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

  it("切走不停止服务器任务：原会话仍在运行", async () => {
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
    expect(api.stopTurn).not.toHaveBeenCalled();
  });

  it("停止只发请求，状态等终态事件再变", async () => {
    api.streamEvents.mockImplementation(async function* () {
      yield { seq: 1, at: "", type: "turn_started", text: "写第一章" };
      await new Promise(() => undefined);
    });
    await useConversationsStore.getState().load("w1");
    void useConversationsStore.getState().send("写第一章");
    await flush();

    await useConversationsStore.getState().stop();

    expect(api.stopTurn).toHaveBeenCalledWith("w1", "t1");
    expect(useConversationsStore.getState().entries.c1?.running).toBe(true);
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
      { set_draft: true, draft_target: "c1", draft_text: "先写开头" },
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
      { set_draft: true, draft_target: "c1", draft_text: "我的草稿" },
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
});
