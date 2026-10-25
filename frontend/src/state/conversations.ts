import { errorMessage } from "../api/errors";
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
  EMPTY_CONTEXT,
  type ActiveTurn,
  type ContextInput,
  type Conversation,
  type ConversationUi,
  type DraftContext,
  type RunPhase,
  type TurnInput,
  type WorkspaceEvent,
} from "../api/workspace";
import {
  appendEvent,
  buildTurns,
  NeedsConversationReload,
  type ChatTurn,
} from "./chatMessages";
import { createStreamBatcher } from "./streamBatcher";

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

/** 发送选项：索引、检查与身份核对都由界面明确发起，不靠模型从文字里猜。 */
export interface SendOptions {
  purpose: "chat" | "update_chapter_index" | "review" | "update_disclosure" | "update_state";
  indexPaths?: string[];
  reviewPaths?: string[];
}

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
  /** 草稿携带的参考：与文字同一次保存，删除文字时一并删除。 */
  draftContexts: Record<string, DraftContext>;
  /** 草稿携带的灵感修订：只在本机草稿槽位里，发送时随本轮提交。 */
  draftIdeas: Record<string, DraftIdea[]>;
  revision: string;
  error: string | null;
  pendingKey: string | null;

  load(workspaceId: string): Promise<void>;
  reset(): void;
  refreshList(): Promise<void>;
  rememberRecent(conversationId: string): Promise<void>;
  newConversation(): void;
  select(conversationId: string): Promise<void>;
  send(text: string, context?: ContextInput, options?: SendOptions): Promise<void>;
  /** 修改当前草稿的参考选择；只选文件、还没写文字时也保留。 */
  setDraftContext(selectedPath: string | null, referencePaths: string[]): void;
  /** 目录里「加入对话」：把一份文件追加到当前草稿的参考，重复点击不重复登记。 */
  addReference(path: string): void;
  /** 灵感「带到对话」：只登记本轮参考，不发送、不改正文。 */
  addIdeaReference(input: IdeaReferenceInput): void;
  /** 作者明确把一条引用换成新修订；同一条目不叠出两个引用。 */
  updateIdeaReference(input: IdeaReferenceInput): void;
  removeIdeaReference(ideaId: string): void;
  stop(): Promise<void>;
  /** 断线后重新接收：先取活动快照，再从确认过的位置继续。 */
  reconnect(): Promise<void>;
  rename(conversationId: string, title: string): Promise<void>;
  setDraft(text: string): void;
  saveDraft(): Promise<void>;
  dismissError(): void;
}

function describe(error: unknown): string {
  return errorMessage(error, "对话操作");
}

function entryKey(conversationId: string | null): string {
  return conversationId ?? NEW_DRAFT;
}

/** 两份参考列表是否完全相同：顺序即选择顺序，所以不排序比较。 */
function samePathList(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((path, index) => path === right[index]);
}

export interface DraftIdea {
  idea_id: string;
  revision_id: string;
  /** 原文短预览：显示用，不能当编辑原文，也不写回服务端。 */
  preview: string;
  /** 正文字符数，用来说明这一条引用覆盖多少原文。 */
  chars: number;
}

export interface IdeaReferenceInput {
  ideaId: string;
  revisionId: string;
  preview: string;
  chars: number;
}

function sameIdeaList(left: DraftIdea[], right: DraftIdea[]): boolean {
  return (
    left.length === right.length &&
    left.every(
      (item, index) =>
        item.idea_id === right[index].idea_id &&
        item.revision_id === right[index].revision_id,
    )
  );
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// 网络中断后的有限退避：不做无限重连，连续失败就把入口交给作者。
const RECONNECT_DELAYS = [0, 1000, 2000, 5000];

/** 正在回读日志的会话：缺口一次只补一次，避免重复请求。 */
const reloading = new Set<string>();
/** 回读在途时收到的实时事件：快照落地后按 seq 接上，不让旧快照抹掉新事件。 */
const bufferedDuringReload = new Map<string, WorkspaceEvent[]>();

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

let workspaceLoadEpoch = 0;
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

  function applyEvents(conversationId: string, events: WorkspaceEvent[]): void {
    const entry = get().entries[conversationId] ?? EMPTY_ENTRY;
    let { turns, lastSeq, phase } = entry;
    let needsReload = false;
    for (const event of events) {
      // 每批内部也按 seq 去重，分片仍逐条归并；只在全部处理后通知一次界面。
      if (event.seq <= lastSeq) continue;
      try {
        turns = appendEvent(turns, event);
      } catch (error) {
        if (!(error instanceof NeedsConversationReload)) throw error;
        needsReload = true;
        continue;
      }
      lastSeq = event.seq;
      if (event.type === "run_phase") phase = event.phase;
    }
    if (lastSeq !== entry.lastSeq) patchEntry(conversationId, { turns, lastSeq, phase });
    if (needsReload) void reloadLog(conversationId);
  }

  function appendEventsTo(conversationId: string, events: WorkspaceEvent[]): void {
    // 回读在途：照常显示，同时记下这一段。快照代表请求发出那一刻的日志位置，
    // 比实时流落后；快照落地后按 seq 把这些事件接上，游标才不会倒退、
    // 已经收到的完成结果也不会被旧快照抹掉。
    if (reloading.has(conversationId)) {
      const buffered = bufferedDuringReload.get(conversationId) ?? [];
      buffered.push(...events);
      bufferedDuringReload.set(conversationId, buffered);
    }
    applyEvents(conversationId, events);
  }

  async function reloadLog(conversationId: string): Promise<void> {
    const workspaceId = get().workspaceId;
    if (!workspaceId || reloading.has(conversationId)) return;
    reloading.add(conversationId);
    bufferedDuringReload.delete(conversationId);
    patchEntry(conversationId, { needsReload: true });
    let again = false;
    try {
      const snapshot = await conversationMessages(workspaceId, conversationId, 0);
      const current = get().entries[conversationId] ?? EMPTY_ENTRY;
      if (snapshot.last_seq < current.lastSeq) {
        // 快照比已经显示的内容还旧：保留现有事件，等下一次缺号再补。
        patchEntry(conversationId, { needsReload: false });
      } else {
        const buffered = bufferedDuringReload.get(conversationId) ?? [];
        let turns = buildTurns(snapshot.events);
        let lastSeq = snapshot.last_seq;
        let phase = current.phase;
        for (const event of buffered) {
          if (event.seq <= lastSeq) continue;
          try {
            turns = appendEvent(turns, event);
          } catch (error) {
            if (error instanceof NeedsConversationReload) {
              // 接上之后仍有缺口：先把已经接好的部分落地，再回读一次。
              again = true;
              break;
            }
            throw error;
          }
          lastSeq = event.seq;
          if (event.type === "run_phase") phase = event.phase;
        }
        patchEntry(conversationId, { turns, lastSeq, phase, needsReload: false });
      }
    } catch (error) {
      patchEntry(conversationId, { needsReload: false, error: describe(error) });
    }
    bufferedDuringReload.delete(conversationId);
    reloading.delete(conversationId);
    if (again) void reloadLog(conversationId);
  }

  async function consume(conversationId: string, after: number): Promise<void> {
    const workspaceId = get().workspaceId;
    if (!workspaceId) return;
    const generation = beginSubscription(conversationId);
    const controller = new AbortController();
    controllers.get(conversationId)?.abort();
    controllers.set(conversationId, controller);
    const batcher = createStreamBatcher((events) => {
      if (get().workspaceId === workspaceId && isCurrentSubscription(conversationId, generation)) {
        appendEventsTo(conversationId, events);
      }
    });
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
          batcher.push(event);
          cursor = Math.max(cursor, event.seq);
          if (event.type === "run_finished") {
            finished = true;
            break;
          }
        }
      } catch (error) {
        batcher.flush();
        if (controller.signal.aborted || !isCurrentSubscription(conversationId, generation)) {
          break;
        }
        // 连接断开不等于任务结束：保留运行状态，退避后再接一次。
        patchEntry(conversationId, { error: `连接中断：${describe(error)}` });
        continue;
      }
      batcher.flush();
      if (finished) break;
      // 流正常结束却没有整轮终态：服务端可能重启过，按最后确认的位置再接一次。
      if (isCurrentSubscription(conversationId, generation)) {
        patchEntry(conversationId, { error: "连接中断，正在重新接收…" });
      }
    }
    batcher.flush();
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
    const { workspaceId, drafts, draftContexts } = get();
    if (!workspaceId) return;
    const text = drafts[key] ?? "";
    const context = (draftContexts ?? {})[key];
    // 文字与参考放在同一个 PATCH 里：文字清空时引用不会残留成没有内容的悬空选择。
    const patch = {
      set_draft: true,
      draft_target: key,
      draft_text: text,
      set_draft_context: true,
      draft_selected_path: context?.selected_path ?? null,
      draft_reference_paths: context?.reference_paths ?? [],
    };
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
    draftContexts: {},
    draftIdeas: {},
    revision: "0",
    error: null,
    pendingKey: null,

    async load(workspaceId) {
      get().reset();
      set({ workspaceId });
      const loadEpoch = workspaceLoadEpoch;
      try {
        const [list, ui] = await Promise.all([
          listConversations(workspaceId),
          fetchConversationUi(workspaceId),
        ]);
        if (workspaceLoadEpoch !== loadEpoch || get().workspaceId !== workspaceId) return;
        set({
          items: list.items,
          recentId: list.recent_id,
          drafts: ui.drafts,
          draftContexts: ui.draft_contexts ?? {},
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
        if (workspaceLoadEpoch === loadEpoch && get().workspaceId === workspaceId) set({ error: describe(error) });
      }
    },

    reset() {
      workspaceLoadEpoch += 1;
      for (const controller of controllers.values()) controller.abort();
      controllers.clear();
      generations.clear();
      reloading.clear();
      bufferedDuringReload.clear();
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
        draftContexts: {},
        draftIdeas: {},
        revision: "0",
        error: null,
        pendingKey: null,
      });
    },

    async refreshList() {
      const workspaceId = get().workspaceId;
      if (!workspaceId) return;
      const loadEpoch = workspaceLoadEpoch;
      try {
        const list = await listConversations(workspaceId);
        if (workspaceLoadEpoch !== loadEpoch || get().workspaceId !== workspaceId) return;
        set({ items: list.items, recentId: list.recent_id });
        const active = list.active_turn;
        if (active) {
          patchEntry(active.conversation_id, { running: true, activeTurn: active });
        }
      } catch (error) {
        if (workspaceLoadEpoch === loadEpoch && get().workspaceId === workspaceId) set({ error: describe(error) });
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

    async send(text, context, options) {
      const workspaceId = get().workspaceId;
      const trimmed = text.trim();
      if (!workspaceId || !trimmed || get().pendingKey) return;
      const target = get().selectedId;
      const key = target ?? NEW_DRAFT;
      const keyRequest = `ui-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
      // 请求对象在发送前生成一次：重试与回调都复用同一个快照，不把当前选择偷偷带进去。
      const submittedIdeas = [...(get().draftIdeas[key] ?? [])];
      const request: TurnInput = {
        text: trimmed,
        request_key: keyRequest,
        context: {
          ...(context ?? EMPTY_CONTEXT),
          idea_references: submittedIdeas.map(({ idea_id, revision_id }) => ({
            idea_id,
            revision_id,
          })),
        },
        ...(options &&
        (options.purpose === "update_chapter_index" ||
          options.purpose === "update_disclosure" ||
          options.purpose === "update_state")
          ? { purpose: options.purpose, index_paths: options.indexPaths ?? [] }
          : {}),
        ...(options && options.purpose === "review"
          ? { purpose: options.purpose, review_paths: options.reviewPaths ?? [] }
          : {}),
      };
      set({ pendingKey: keyRequest, error: null });
      // 提交时带上的参考：清空草稿时用它判断「作者有没有在受理期间改动过参考」。
      const submittedRefs = [...(request.context?.reference_paths ?? [])];
      try {
        let conversationId = target;
        let after = conversationId ? (get().entries[conversationId]?.lastSeq ?? 0) : 0;
        if (conversationId) {
          const turn = await startTurn(workspaceId, conversationId, request);
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
          const accepted = await createConversationWithText(workspaceId, request);
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
          // 首次受理把新草稿交给正式会话：不覆盖作者另开的新草稿，也不擦掉发送期间
          // 新选的参考——只有参考仍是这次提交的那一份时，才随文字一起清掉。
          if (get().drafts[NEW_DRAFT] === trimmed) {
            set((state) => {
              const next = { ...state.drafts };
              delete next[NEW_DRAFT];
              const contexts = { ...state.draftContexts };
              const current = contexts[NEW_DRAFT];
              if (!current || samePathList(current.reference_paths ?? [], submittedRefs)) {
                // 新草稿连同它的参考一起交给正式会话，不在槽位里留下悬空引用。
                delete contexts[NEW_DRAFT];
              }
              const ideas = { ...state.draftIdeas };
              const currentIdeas = ideas[NEW_DRAFT];
              if (!currentIdeas || sameIdeaList(currentIdeas, submittedIdeas)) {
                delete ideas[NEW_DRAFT];
              }
              return { drafts: next, draftContexts: contexts, draftIdeas: ideas };
            });
            void persistDraft(NEW_DRAFT);
          }
        }
        // 只清除「仍然等于提交文本」的草稿：受理期间作者新输入的字不能被旧响应擦掉。
        if ((get().drafts[key] ?? "") === trimmed) {
          set((state) => {
            const next = { ...state.drafts };
            delete next[key];
            const contexts = { ...state.draftContexts };
            // 参考随文字一起交给这一轮；发送期间新选的参考要留下，不能被旧响应擦掉。
            const current = contexts[key];
            if (!current || samePathList(current.reference_paths ?? [], submittedRefs)) {
              delete contexts[key];
            }
            const ideas = { ...state.draftIdeas };
            const currentIdeas = ideas[key];
            if (!currentIdeas || sameIdeaList(currentIdeas, submittedIdeas)) {
              delete ideas[key];
            }
            return { drafts: next, draftContexts: contexts, draftIdeas: ideas };
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

    setDraftContext(selectedPath, referencePaths) {
      const key = entryKey(get().selectedId);
      set((state) => ({
        draftContexts: {
          ...state.draftContexts,
          [key]: { selected_path: selectedPath, reference_paths: [...referencePaths] },
        },
      }));
      scheduleDraftSave();
    },

    addReference(path) {
      const key = entryKey(get().selectedId);
      const context = get().draftContexts[key];
      const current = context?.reference_paths ?? [];
      // 目录入口只是追加：已经选过的路径不重复登记，也不打乱作者的选择顺序。
      if (current.includes(path)) return;
      get().setDraftContext(context?.selected_path ?? null, [...current, path]);
    },

    addIdeaReference(input) {
      const key = entryKey(get().selectedId);
      const current = get().draftIdeas[key] ?? [];
      const index = current.findIndex((item) => item.idea_id === input.ideaId);
      const next: DraftIdea = {
        idea_id: input.ideaId,
        revision_id: input.revisionId,
        preview: input.preview,
        chars: input.chars,
      };
      // 同一条目再次加入：同修订忽略，换修订就原位更新，不叠加第二个引用。
      if (index >= 0 && current[index].revision_id === input.revisionId) return;
      const items = index >= 0 ? current.map((item, at) => (at === index ? next : item)) : [...current, next];
      set((state) => ({ draftIdeas: { ...state.draftIdeas, [key]: items } }));
    },

    updateIdeaReference(input) {
      get().addIdeaReference(input);
    },

    removeIdeaReference(ideaId) {
      const key = entryKey(get().selectedId);
      const current = get().draftIdeas[key] ?? [];
      set((state) => ({
        draftIdeas: {
          ...state.draftIdeas,
          [key]: current.filter((item) => item.idea_id !== ideaId),
        },
      }));
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
