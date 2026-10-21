/**
 * 会话状态（0.4 V04-10）。
 *
 * 每个作品一组会话，每个会话一份独立状态：事件、运行中的轮次、草稿。当前选中项只决定
 * 「显示哪一份」，不决定事件归属——切走不会停止服务器任务，切回来按快照接上。
 *
 * 三件事在这里被明确区分：
 * - **正式会话**：只有「第一条非空提示被后端受理」才存在，列表里才出现；
 * - **新对话草稿**：选中 `null` 就是临时新状态，输入保存在 `new` 槽位，不建会话；
 * - **草稿**：不是已发送的提示，不进模型上下文；受理确认后才清除已提交的那一版。
 */

import { create } from "zustand";

import {
  conversationMessages,
  createConversationWithText,
  fetchConversationUi,
  listConversations,
  patchConversationUi,
  renameConversation,
  startTurn,
  stopTurn,
  streamEvents,
  WorkspaceApiError,
  type ActiveTurn,
  type Conversation,
  type ConversationUi,
  type RunPhase,
  type WorkspaceEvent,
} from "../api/workspace";
import {
  appendEvent,
  buildTurns,
  NeedsConversationReload,
  type ChatTurn,
} from "./chatMessages";

export interface ConversationEntry {
  /** 按轮次归并后的展示结构；实时增量与历史回读共用同一套规则。 */
  turns: ChatTurn[];
  /** 已经处理到的事件位置：订阅从这里继续，不重复接收。 */
  lastSeq: number;
  /** 应用确认的运行阶段；没有可靠信息时为 null。 */
  phase: RunPhase | null;
  running: boolean;
  activeTurn: ActiveTurn | null;
  loading: boolean;
  error: string | null;
  /** 增量批次缺号，正在回读本会话日志。 */
  needsReload: boolean;
}

/** 新对话草稿的固定槽位。 */
export const NEW_DRAFT = "new";

const EMPTY_ENTRY: ConversationEntry = {
  turns: [],
  lastSeq: 0,
  phase: null,
  running: false,
  activeTurn: null,
  loading: false,
  error: null,
  needsReload: false,
};

export interface ConversationsStore {
  workspaceId: string | null;
  items: Conversation[];
  recentId: string | null;
  /** `null` 表示临时新状态：还没有正式会话。 */
  selectedId: string | null;
  entries: Record<string, ConversationEntry>;
  drafts: Record<string, string>;
  revision: string;
  error: string | null;
  pendingKey: string | null;

  load(workspaceId: string): Promise<void>;
  reset(): void;
  refreshList(): Promise<void>;
  rememberRecent(conversationId: string): Promise<void>;
  newConversation(): void;
  select(conversationId: string): Promise<void>;
  send(text: string): Promise<void>;
  stop(): Promise<void>;
  /** 断线后重新接收：先取活动快照，再从确认过的位置继续。 */
  reconnect(): Promise<void>;
  rename(conversationId: string, title: string): Promise<void>;
  setDraft(text: string): void;
  saveDraft(): Promise<void>;
  dismissError(): void;
}

function describe(error: unknown): string {
  if (error instanceof WorkspaceApiError) {
    return `${error.why}。${error.fix}`;
  }
  return error instanceof Error ? error.message : "请求失败";
}

function entryKey(conversationId: string | null): string {
  return conversationId ?? NEW_DRAFT;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// 网络中断后的有限退避：不做无限重连，连续失败就把入口交给作者。
const RECONNECT_DELAYS = [0, 1000, 2000, 5000];

/** 正在回读日志的会话：缺口一次只补一次，避免重复请求。 */
const reloading = new Set<string>();

// 每个会话一个订阅代次：关闭旧订阅的 finally 不能改动新订阅的状态。
const generations = new Map<string, number>();
const controllers = new Map<string, AbortController>();

function beginSubscription(key: string): number {
  const next = (generations.get(key) ?? 0) + 1;
  generations.set(key, next);
  return next;
}

function isCurrentSubscription(key: string, generation: number): boolean {
  return generations.get(key) === generation;
}

let draftTimer: ReturnType<typeof setTimeout> | null = null;
/** 草稿写入串行队列：保证同一窗口同时只有一个 PATCH 在途。 */
let draftQueue: Promise<void> = Promise.resolve();
/** 上一次草稿保存失败留下的提示；保存成功后只清它，不误伤其他操作的错误。 */
let draftErrorText: string | null = null;
let onRunFinished: (() => void) | null = null;

/** 一轮真正结束后回读文件与 Git：结果以磁盘为准，不由界面拼接。 */
export function registerRunFinished(handler: () => void): void {
  onRunFinished = handler;
}

function scheduleDraftSave(): void {
  if (draftTimer) clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {
    draftTimer = null;
    void useConversationsStore.getState().saveDraft();
  }, 600);
}

export const useConversationsStore = create<ConversationsStore>()((set, get) => {
  function patchEntry(conversationId: string, patch: Partial<ConversationEntry>): void {
    set((state) => {
      const current = state.entries[conversationId] ?? EMPTY_ENTRY;
      return { entries: { ...state.entries, [conversationId]: { ...current, ...patch } } };
    });
  }

  function appendEventTo(conversationId: string, event: WorkspaceEvent): void {
    const entry = get().entries[conversationId] ?? EMPTY_ENTRY;
    // 先按 seq 去重：断开重连、回读与实时推送可能送到同一条。
    if (event.seq <= entry.lastSeq) return;
    const patch: Partial<ConversationEntry> = { lastSeq: event.seq };
    if (event.type === "run_phase") patch.phase = event.phase;
    try {
      patch.turns = appendEvent(entry.turns, event);
    } catch (error) {
      if (error instanceof NeedsConversationReload) {
        // 批次缺号：回读本会话日志补齐，不重发用户请求。
        void reloadLog(conversationId);
        return;
      }
      throw error;
    }
    patchEntry(conversationId, patch);
  }

  async function reloadLog(conversationId: string): Promise<void> {
    const workspaceId = get().workspaceId;
    if (!workspaceId || reloading.has(conversationId)) return;
    reloading.add(conversationId);
    patchEntry(conversationId, { needsReload: true });
    try {
      const snapshot = await conversationMessages(workspaceId, conversationId, 0);
      patchEntry(conversationId, {
        turns: buildTurns(snapshot.events),
        lastSeq: snapshot.last_seq,
        needsReload: false,
      });
    } catch (error) {
      patchEntry(conversationId, { needsReload: false, error: describe(error) });
    } finally {
      reloading.delete(conversationId);
    }
  }

  async function consume(conversationId: string, after: number): Promise<void> {
    const workspaceId = get().workspaceId;
    if (!workspaceId) return;
    const generation = beginSubscription(conversationId);
    const controller = new AbortController();
    controllers.get(conversationId)?.abort();
    controllers.set(conversationId, controller);
    let cursor = after;
    let finished = false;
    for (const delay of RECONNECT_DELAYS) {
      if (delay) await sleep(delay);
      if (controller.signal.aborted) break;
      // 事件只属于发起订阅的那个会话：代次不符就停，避免旧流污染新视图。
      if (!isCurrentSubscription(conversationId, generation)) return;
      try {
        for await (const event of streamEvents(
          workspaceId,
          conversationId,
          cursor,
          controller.signal,
        )) {
          if (!isCurrentSubscription(conversationId, generation)) break;
          appendEventTo(conversationId, event);
          cursor = Math.max(cursor, event.seq);
          if (event.type === "run_finished") {
            finished = true;
            break;
          }
        }
      } catch (error) {
        if (controller.signal.aborted || !isCurrentSubscription(conversationId, generation)) {
          break;
        }
        // 连接断开不等于任务结束：保留运行状态，退避后再接一次。
        patchEntry(conversationId, { error: `连接中断：${describe(error)}` });
        continue;
      }
      if (finished) break;
      // 流正常结束却没有整轮终态：服务端可能重启过，按最后确认的位置再接一次。
      if (isCurrentSubscription(conversationId, generation)) {
        patchEntry(conversationId, { error: "连接中断，正在重新接收…" });
      }
    }
    if (controllers.get(conversationId) === controller) controllers.delete(conversationId);
    if (!isCurrentSubscription(conversationId, generation)) return;
    if (finished) {
      patchEntry(conversationId, {
        running: false,
        activeTurn: null,
        phase: null,
        error: null,
      });
      onRunFinished?.();
      void get().refreshList();
    }
  }

  async function persistDraft(key: string): Promise<void> {
    // 草稿会被定时器、切换会话、发送受理多次触发；串行化后每次写入都读到上一个
    // 写入返回的版本号，避免同一窗口的两个请求互相判成「另一端改过」。
    const run = draftQueue.then(() => writeDraft(key));
    draftQueue = run.catch(() => undefined);
    return run;
  }

  async function writeDraft(key: string): Promise<void> {
    const { workspaceId, drafts } = get();
    if (!workspaceId) return;
    const text = drafts[key] ?? "";
    const patch = { set_draft: true, draft_target: key, draft_text: text };
    try {
      const ui: ConversationUi = await patchConversationUi(
        workspaceId,
        patch,
        get().revision,
      ).catch(async (error: unknown) => {
        if (!(error instanceof WorkspaceApiError && error.code === "CONVERSATION_UI_CONFLICT")) {
          throw error;
        }
        // 版本号过期：拿当前版本号立即重发一次。草稿还在本地，不需要作者再输入一遍。
        const fresh = await fetchConversationUi(workspaceId);
        return patchConversationUi(workspaceId, patch, fresh.revision);
      });
      // 保存成功后撤销上一次的失败提示：草稿已经落盘，继续挂着旧提示会让人以为一直在失败。
      if (draftErrorText !== null && get().error === draftErrorText) {
        set({ revision: ui.revision, error: null });
      } else {
        set({ revision: ui.revision });
      }
      draftErrorText = null;
    } catch (error) {
      if (error instanceof WorkspaceApiError && error.code === "CONVERSATION_UI_CONFLICT") {
        // 重发仍冲突：说明另一端在这期间又写了，保留本地文字等下一次编辑。
        draftErrorText =
          "这段草稿与另一端不同，已保留在这里；重新输入会按这里的内容保存。";
      } else {
        draftErrorText = `草稿还没保存：${describe(error)}`;
      }
      set({ error: draftErrorText });
    }
  }

  return {
    workspaceId: null,
    items: [],
    recentId: null,
    selectedId: null,
    entries: {},
    drafts: {},
    revision: "0",
    error: null,
    pendingKey: null,

    async load(workspaceId) {
      get().reset();
      set({ workspaceId });
      try {
        const [list, ui] = await Promise.all([
          listConversations(workspaceId),
          fetchConversationUi(workspaceId),
        ]);
        set({
          items: list.items,
          recentId: list.recent_id,
          drafts: ui.drafts,
          revision: ui.revision,
        });
        if (list.active_turn) {
          // 服务器上还有一轮在跑：先把它标到所属会话，切过去就能看到停止按钮。
          const cid = list.active_turn.conversation_id;
          patchEntry(cid, { running: true, activeTurn: list.active_turn });
        }
        // 加载期间作者可能已经自己选了会话：恢复只填空，不抢回选择。
        const target = list.recent_id ?? list.items[0]?.id ?? null;
        if (target && get().selectedId === null) await get().select(target);
      } catch (error) {
        set({ error: describe(error) });
      }
    },

    reset() {
      for (const controller of controllers.values()) controller.abort();
      controllers.clear();
      generations.clear();
      if (draftTimer) clearTimeout(draftTimer);
      draftTimer = null;
      draftQueue = Promise.resolve();
      draftErrorText = null;
      set({
        workspaceId: null,
        items: [],
        recentId: null,
        selectedId: null,
        entries: {},
        drafts: {},
        revision: "0",
        error: null,
        pendingKey: null,
      });
    },

    async refreshList() {
      const workspaceId = get().workspaceId;
      if (!workspaceId) return;
      try {
        const list = await listConversations(workspaceId);
        set({ items: list.items, recentId: list.recent_id });
        const active = list.active_turn;
        if (active) {
          patchEntry(active.conversation_id, { running: true, activeTurn: active });
        }
      } catch (error) {
        set({ error: describe(error) });
      }
    },

    newConversation() {
      if (!get().workspaceId) return;
      void get().saveDraft();
      for (const controller of controllers.values()) controller.abort();
      controllers.clear();
      set({ selectedId: null });
      // 重复点「新对话」复用同一份草稿：不堆积空白会话。
    },

    async select(conversationId) {
      const workspaceId = get().workspaceId;
      if (!workspaceId || get().selectedId === conversationId) return;
      const previous = get().selectedId;
      // 先切视图再读：慢请求回来时代次已经变了，不会把作者选中的会话抢回去。
      set({ selectedId: conversationId });
      patchEntry(conversationId, { loading: true, error: null });
      // 旧会话的草稿按它自己的键保存，不跟着新选择走。
      if (previous) await persistDraft(entryKey(previous));
      try {
        const snapshot = await conversationMessages(workspaceId, conversationId, 0);
        // 迟到的响应只更新它自己的会话，不把界面切回去。
        patchEntry(conversationId, {
          turns: buildTurns(snapshot.events),
          lastSeq: snapshot.last_seq,
          phase: null,
          running: Boolean(snapshot.active_turn),
          activeTurn: snapshot.active_turn,
          loading: false,
          error: null,
          needsReload: false,
        });
        if (get().selectedId === conversationId) void get().rememberRecent(conversationId);
        if (snapshot.active_turn) await consume(conversationId, snapshot.last_seq);
      } catch (error) {
        patchEntry(conversationId, { loading: false, error: describe(error) });
      }
    },

    async rememberRecent(conversationId) {
      const { workspaceId, revision } = get();
      if (!workspaceId) return;
      try {
        const ui = await patchConversationUi(
          workspaceId,
          { set_recent: true, recent_id: conversationId },
          revision,
        );
        set({ revision: ui.revision, recentId: ui.recent_id });
      } catch {
        // 记不住「上次看的会话」不算失败：下次按最近活动选一个。
      }
    },

    async send(text) {
      const workspaceId = get().workspaceId;
      const trimmed = text.trim();
      if (!workspaceId || !trimmed || get().pendingKey) return;
      const target = get().selectedId;
      const key = target ?? NEW_DRAFT;
      const keyRequest = `ui-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
      set({ pendingKey: keyRequest, error: null });
      try {
        let conversationId = target;
        let after = conversationId ? (get().entries[conversationId]?.lastSeq ?? 0) : 0;
        if (conversationId) {
          const turn = await startTurn(workspaceId, conversationId, trimmed, keyRequest);
          patchEntry(conversationId, {
            running: true,
            activeTurn: {
              conversation_id: conversationId,
              turn_id: turn.turn_id,
              state: "accepted",
              model: "",
              connection: "",
              started_at: turn.started_at,
            },
          });
        } else {
          const accepted = await createConversationWithText(workspaceId, trimmed, keyRequest);
          conversationId = accepted.conversation_id;
          after = 0;
          set((state) => ({
            selectedId: conversationId,
            items: [
              {
                id: accepted.conversation_id,
                title: accepted.title,
                created_at: accepted.started_at,
                updated_at: accepted.started_at,
              },
              ...state.items,
            ],
          }));
          patchEntry(conversationId, {
            turns: [],
            lastSeq: 0,
            phase: null,
            running: true,
            activeTurn: {
              conversation_id: conversationId,
              turn_id: accepted.turn_id,
              state: "accepted",
              model: "",
              connection: "",
              started_at: accepted.started_at,
            },
          });
          // 首次受理把新草稿交给正式会话：不覆盖作者另开的新草稿。
          if (get().drafts[NEW_DRAFT] === trimmed) {
            set((state) => {
              const next = { ...state.drafts };
              delete next[NEW_DRAFT];
              return { drafts: next };
            });
            void persistDraft(NEW_DRAFT);
          }
        }
        // 只清除「仍然等于提交文本」的草稿：受理期间作者新输入的字不能被旧响应擦掉。
        if ((get().drafts[key] ?? "") === trimmed) {
          set((state) => {
            const next = { ...state.drafts };
            delete next[key];
            return { drafts: next };
          });
          void persistDraft(key);
        }
        await consume(conversationId, after);
      } catch (error) {
        // 受理前失败：不清草稿、不多建会话，保留输入让作者重试。
        set({ error: describe(error) });
      } finally {
        set({ pendingKey: null });
      }
    },

    async stop() {
      const workspaceId = get().workspaceId;
      const selected = get().selectedId;
      if (!workspaceId || !selected) return;
      const entry = get().entries[selected];
      const turnId = entry?.activeTurn?.turn_id;
      if (!turnId) return;
      try {
        await stopTurn(workspaceId, turnId);
      } catch (error) {
        set({ error: describe(error) });
      }
    },

    async reconnect() {
      const workspaceId = get().workspaceId;
      const selected = get().selectedId;
      if (!workspaceId || !selected) return;
      // 先取活动快照，再从最后确认的位置继续：断开期间保存的事件不会丢。
      try {
        const snapshot = await conversationMessages(workspaceId, selected, 0);
        patchEntry(selected, {
          turns: buildTurns(snapshot.events),
          lastSeq: snapshot.last_seq,
          running: Boolean(snapshot.active_turn),
          activeTurn: snapshot.active_turn,
          error: null,
          needsReload: false,
        });
        if (snapshot.active_turn) await consume(selected, snapshot.last_seq);
      } catch (error) {
        patchEntry(selected, { error: describe(error) });
      }
    },

    async rename(conversationId, title) {
      const workspaceId = get().workspaceId;
      const clean = title.trim();
      if (!workspaceId || !clean) return;
      try {
        const updated = await renameConversation(workspaceId, conversationId, clean);
        set((state) => ({
          items: state.items.map((item) => (item.id === updated.id ? updated : item)),
        }));
      } catch (error) {
        set({ error: describe(error) });
      }
    },

    setDraft(text) {
      const key = entryKey(get().selectedId);
      set((state) => ({ drafts: { ...state.drafts, [key]: text } }));
      scheduleDraftSave();
    },

    async saveDraft() {
      if (draftTimer) {
        clearTimeout(draftTimer);
        draftTimer = null;
      }
      await persistDraft(entryKey(get().selectedId));
    },

    dismissError() {
      set({ error: null });
    },
  };
});

/** 当前选中会话的状态；新对话状态返回空条目。 */
export function selectedEntry(state: ConversationsStore): ConversationEntry {
  return state.entries[entryKey(state.selectedId)] ?? EMPTY_ENTRY;
}

/** 当前选中项对应的草稿键。 */
export function draftKeyOf(state: ConversationsStore): string {
  return entryKey(state.selectedId);
}

/** 正在运行的那个会话（可能是别的会话）。 */
export function runningConversation(state: ConversationsStore): Conversation | null {
  const running = Object.entries(state.entries).find(([, entry]) => entry.running);
  if (!running) return null;
  const [conversationId] = running;
  return state.items.find((item) => item.id === conversationId) ?? null;
}
