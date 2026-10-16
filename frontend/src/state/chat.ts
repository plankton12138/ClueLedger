/**
 * 对话状态（Phase 19.2）：按 project / conversation / turn 索引，事件经纯 reducer 归约。
 *
 * 设计要点（对应计划 §19.2 与 §4.1）：
 * - **规范化索引**：`views[conversationId]` 各自持有消息、轮次、序号游标与活动轮次；
 *   流式消息只进对话视图，绝不写入正文缓冲（`state/v2.ts` 的 chapter 编辑状态）；
 * - **纯 reducer**：`applyChatEvent(view, envelope)` 无副作用、不修改入参，
 *   因此去重、迟到分片、终态收束都能脱离组件单测；
 * - **服务可替换**：模拟服务与真实 API 实现同一 `ChatService`，由能力探测决定；
 *   缺少新版端点时进入 unsupported 并给出重启指引，不显示假空列表；
 * - **结果未知**：网络故障不当作失败——保留幂等键，用同一请求重试，服务端
 *   若已接受则返回同一轮次，不重复调用模型；
 * - 断线只改连接状态，不把模型执行标记成失败；重连后以服务端为准补偿。
 */

import { create } from "zustand";
import {
  probeChatCapability,
  type ChatCapabilityProbe,
  type ChatConnectionStatus,
  type ChatConversation,
  type ChatEventEnvelope,
  type ChatMessage,
  type ChatReference,
  type ChatService,
  type ChatTurn,
  type MessageStatus,
  type TaskDraftPayload,
  type TaskExecutionPayload,
  type TaskKind,
  type CandidatePayload,
} from "../api/chat";
import { SimulatorService } from "../api/chatSimulator";
import { V2Error } from "../api/v2";
import { isDirty, useV2Store } from "./v2";

/** 开发模式或显式 `?chatMock=1` 才装配模拟服务；生产构建不展示虚假成功。 */
export function mockEnabled(): boolean {
  if (import.meta.env.DEV) return true;
  if (typeof location === "undefined") return false;
  return new URLSearchParams(location.search).get("chatMock") === "1";
}

export const simulator = new SimulatorService();

// ---------------------------------------------------------------------------
// 会话视图与纯 reducer
// ---------------------------------------------------------------------------

/** 发送结果未知：保留原始请求与幂等键，等待作者用同一请求重试。 */
export interface PendingSend {
  idempotency_key: string;
  text: string;
  refs: ChatReference[];
  error: string;
}

export interface ConversationView {
  messages: ChatMessage[];
  turns: Record<string, ChatTurn>;
  abandoned: Record<string, boolean>;
  /** 已应用的最高事件序号（仅在会话内递增）。 */
  cursor: number;
  activeTurnId: string | null;
  pending: PendingSend | null;
  loading: boolean;
  error: string | null;
  connection: ChatConnectionStatus;
  connectionDetail: string | null;
}

export const NO_MESSAGES: ChatMessage[] = [];
export const NO_TURNS: Record<string, ChatTurn> = {};
export const NO_ABANDONED: Record<string, boolean> = {};
export const NO_REFS: ChatReference[] = [];
export const NO_CONVERSATIONS: ChatConversation[] = [];

export function emptyView(): ConversationView {
  return {
    messages: [],
    turns: {},
    abandoned: {},
    cursor: 0,
    activeTurnId: null,
    pending: null,
    loading: false,
    error: null,
    connection: "connecting",
    connectionDetail: null,
  };
}

const patchMessage = (
  messages: ChatMessage[],
  messageId: string,
  patch: Partial<ChatMessage>,
): ChatMessage[] =>
  messages.map((m) => (m.message_id === messageId ? { ...m, ...patch } : m));

const streamingOf = (messages: ChatMessage[], turnId: string) =>
  messages.find(
    (m) => m.turn_id === turnId && m.role === "assistant" && m.status === "streaming",
  );

/** 终态：把该轮次的流式消息收束为给定状态，并清空活动轮次。 */
function settle(
  view: ConversationView,
  turnId: string,
  status: MessageStatus,
  error: ChatMessage["error"] = null,
): ChatMessage[] {
  return view.messages.map((m) =>
    m.turn_id === turnId && m.role === "assistant" && m.status === "streaming"
      ? { ...m, status, error }
      : m,
  );
}

/**
 * 纯归约：把一条事件信封应用到会话视图。
 *
 * 不变式：
 * - 带序号的事件按 `cursor` 去重（序号 0 表示服务端未分配，不参与去重）；
 * - 非运行态轮次的 `message.delta` 一律丢弃，**且不推进游标**（停止后迟到分片无效）；
 * - 终态事件先冲刷已收到的正文再落状态，部分回复不丢。
 */
export function applyChatEvent(
  view: ConversationView,
  envelope: ChatEventEnvelope,
): ConversationView {
  const event = envelope.event;
  const sequenced = envelope.sequence > 0;
  const stale = sequenced && envelope.sequence <= view.cursor;
  const next = (patch: Partial<ConversationView>): ConversationView => ({
    ...view,
    ...patch,
    cursor: sequenced ? Math.max(view.cursor, envelope.sequence) : view.cursor,
  });
  if (stale && event.type !== "message.delta") return view;

  switch (event.type) {
    case "turn.accepted": {
      const turn = event.turn;
      return next({
        turns: { ...view.turns, [turn.turn_id]: turn },
        activeTurnId: turn.turn_id,
      });
    }
    case "turn.started": {
      const turn = view.turns[event.turn_id];
      const turns = turn
        ? { ...view.turns, [event.turn_id]: { ...turn, status: "running" as const } }
        : view.turns;
      if (view.messages.some((m) => m.message_id === event.message_id))
        return next({ turns, activeTurnId: event.turn_id });
      const userMessage = view.messages.find(
        (m) => m.turn_id === event.turn_id && m.role === "author",
      );
      const placeholder: ChatMessage = {
        message_id: event.message_id,
        conversation_id: envelope.conversation_id,
        turn_id: event.turn_id,
        role: "assistant",
        kind: "text",
        text: "",
        refs: userMessage?.refs ?? [],
        model: event.model,
        status: "streaming",
        error: null,
        created_at: new Date().toISOString(),
      };
      return next({
        turns,
        messages: [...view.messages, placeholder],
        activeTurnId: event.turn_id,
      });
    }
    case "message.delta": {
      if (stale) return view;
      const turn = view.turns[event.turn_id];
      // 停止/失败后的迟到分片无效，也不推进游标。
      if (!turn || turn.status !== "running") return view;
      const target = streamingOf(view.messages, event.turn_id);
      if (!target) return next({});
      return next({
        messages: patchMessage(view.messages, target.message_id, {
          text: target.text + event.text,
        }),
      });
    }
    case "message.completed": {
      if (stale) return view;
      const turn = view.turns[event.turn_id];
      const target = streamingOf(view.messages, event.turn_id);
      return next({
        turns: turn
          ? {
              ...view.turns,
              [event.turn_id]: { ...turn, status: "completed", end_reason: event.end_reason },
            }
          : view.turns,
        messages: target
          ? patchMessage(view.messages, target.message_id, {
              text: event.text,
              status: "completed",
            })
          : view.messages,
        activeTurnId: view.activeTurnId === event.turn_id ? null : view.activeTurnId,
      });
    }
    case "turn.cancelled":
    case "turn.failed":
    case "turn.interrupted": {
      if (stale) return view;
      const turn = view.turns[event.turn_id];
      const status: MessageStatus =
        event.type === "turn.cancelled" ? "stopped" : "failed";
      const error =
        event.type === "turn.failed"
          ? (event.error ?? {
              code: "CHAT_TURN_FAILED",
              category: "environment",
              where: "chat.turn",
              why: event.reason,
              fix: "重试这一轮；已收到的部分回复会保留",
            })
          : null;
      return next({
        turns: turn
          ? {
              ...view.turns,
              [event.turn_id]: {
                ...turn,
                status:
                  event.type === "turn.cancelled"
                    ? "stopped"
                    : event.type === "turn.failed"
                      ? "failed"
                      : "interrupted",
                end_reason: event.reason,
              },
            }
          : view.turns,
        messages: settle(view, event.turn_id, status, error),
        activeTurnId: view.activeTurnId === event.turn_id ? null : view.activeTurnId,
      });
    }
    case "task.draft.created": {
      if (view.messages.some((m) => m.message_id === event.message_id)) return next({});
      const message: ChatMessage = {
        message_id: event.message_id,
        conversation_id: envelope.conversation_id,
        turn_id: envelope.turn_id,
        role: "assistant",
        kind: "task_draft",
        text: "",
        refs: event.draft.refs,
        model: null,
        status: "completed",
        error: null,
        created_at: new Date().toISOString(),
        payload: event.draft,
      };
      return next({ messages: [...view.messages, message] });
    }
    case "task.draft.updated": {
      return next({
        messages: view.messages.map((m) =>
          m.kind === "task_draft" &&
          (m.payload as TaskDraftPayload)?.draft_id === event.draft.draft_id
            ? { ...m, payload: event.draft }
            : m,
        ),
      });
    }
    case "task.draft.abandoned": {
      return next({ abandoned: { ...view.abandoned, [event.draft_id]: true } });
    }
    case "task.linked": {
      if (view.messages.some((m) => m.message_id === event.message_id)) return next({});
      const messages = view.messages.map((m) =>
        m.kind === "task_draft" && (m.payload as TaskDraftPayload).draft_id === event.draft_id
          ? { ...m, payload: { ...(m.payload as TaskDraftPayload), job_ref: event.job_ref } }
          : m,
      );
      const payload: TaskExecutionPayload = {
        job_ref: event.job_ref,
        task_kind: event.task_kind,
        state: "queued",
        stage: "已排队",
        model: event.model,
        draft_id: event.draft_id,
        candidate_message_id: null,
        error: null,
      };
      return next({
        messages: [
          ...messages,
          {
            message_id: event.message_id,
            conversation_id: envelope.conversation_id,
            turn_id: null,
            role: "system",
            kind: "task_execution",
            text: "",
            refs: [],
            model: event.model,
            status: "completed",
            error: null,
            created_at: new Date().toISOString(),
            payload,
          },
        ],
      });
    }
    case "task.updated": {
      const existing = view.messages.find((m) => m.message_id === event.message_id);
      if (existing) return next({ messages: patchMessage(view.messages, event.message_id,
        { payload: event.execution }) });
      return next({ messages: [...view.messages, {
        message_id: event.message_id, conversation_id: envelope.conversation_id,
        turn_id: null, role: "system", kind: "task_execution", text: "", refs: [],
        model: event.execution.model, status: "completed", error: null,
        created_at: new Date().toISOString(), payload: event.execution,
      }] });
    }
    case "candidate.ready": {
      if (view.messages.some((m) => m.message_id === event.message_id)) return next({});
      const messages = view.messages.map((m) => {
        if (m.kind !== "task_execution") return m;
        const payload = m.payload as TaskExecutionPayload;
        if (payload.job_ref !== event.job_ref) return m;
        return {
          ...m,
          payload: {
            ...payload,
            state: "waiting" as const,
            stage: "候选已返回，等待作者审阅",
            candidate_message_id: event.message_id,
          } satisfies TaskExecutionPayload,
        };
      });
      return next({
        messages: [
          ...messages,
          {
            message_id: event.message_id,
            conversation_id: envelope.conversation_id,
            turn_id: null,
            role: "system",
            kind: "candidate",
            text: "",
            refs: [],
            model: null,
            status: "completed",
            error: null,
            created_at: new Date().toISOString(),
            payload: event.candidate satisfies CandidatePayload,
          },
        ],
      });
    }
    case "result.accepted": {
      if (view.messages.some((m) => m.message_id === event.message_id)) return next({});
      const messages = view.messages.map((m) => {
        if (m.kind !== "candidate") return m;
        const payload = m.payload as CandidatePayload;
        if (payload.job_ref !== event.job_ref) return m;
        return { ...m, payload: { ...payload, accepted: true } };
      });
      return next({
        messages: [
          ...messages,
          {
            message_id: event.message_id,
            conversation_id: envelope.conversation_id,
            turn_id: null,
            role: "system",
            kind: "result",
            text: "",
            refs: [],
            model: null,
            status: "completed",
            error: null,
            created_at: new Date().toISOString(),
            payload: event.result,
          },
        ],
      });
    }
  }
}

// ---------------------------------------------------------------------------
// 草稿持久化（输入草稿属于浏览器本地，消息事实与任务状态以服务端为准）
// ---------------------------------------------------------------------------

export const DRAFTS_KEY = "hastings.chat.drafts";

export function loadDrafts(): Record<string, string> {
  try {
    const raw = localStorage.getItem(DRAFTS_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const drafts: Record<string, string> = {};
    for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof value === "string") drafts[key] = value;
    }
    return drafts;
  } catch {
    return {};
  }
}

export function saveDrafts(drafts: Record<string, string>): void {
  try {
    localStorage.setItem(DRAFTS_KEY, JSON.stringify(drafts));
  } catch {
    /* 浏览器禁用存储时仅本次会话可用 */
  }
}

// ---------------------------------------------------------------------------
// 服务选择（能力探测；缺端点 → 重启指引，不假装成功）
// ---------------------------------------------------------------------------

/** 服务选择的可注入依赖（默认：能力探测 + HTTP 客户端）。 */
export interface SelectChatServiceOptions {
  mock?: boolean;
  probe?: (projectId: string) => Promise<ChatCapabilityProbe>;
  createApi?: (projectId: string) => ChatService;
}

export interface ChatServiceSelection {
  kind: "simulator" | "api" | "unsupported";
  service: ChatService | null;
  protocol: string | null;
  detail: string;
}

export async function selectChatService(
  projectId: string,
  options: SelectChatServiceOptions = {},
): Promise<ChatServiceSelection> {
  if (options.mock ?? mockEnabled()) {
    return { kind: "simulator", service: simulator, protocol: "chat.v1", detail: "" };
  }
  const probe = options.probe ?? ((pid: string) => probeChatCapability(pid));
  const result = await probe(projectId);
  if (!result.supported) {
    return { kind: "unsupported", service: null, protocol: null, detail: result.detail };
  }
  const service = options.createApi?.(projectId);
  if (service) {
    return { kind: "api", service, protocol: result.protocol, detail: "" };
  }
  const { createHttpChatService } = await import("../api/chat");
  return {
    kind: "api",
    service: createHttpChatService(projectId),
    protocol: result.protocol,
    detail: "",
  };
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

interface ChatStore {
  serviceKind: "probing" | "simulator" | "api" | "unsupported";
  capabilityDetail: string;
  /** 会话列举/读取失败（与“后端缺少对话端点”区分，可重试）。 */
  loadError: string | null;
  protocol: string | null;
  service: ChatService | null;
  projectId: string | null;
  conversationsByProject: Record<string, ChatConversation[]>;
  activeConversationId: string | null;
  views: Record<string, ConversationView>;
  drafts: Record<string, string>;
  refs: Record<string, ChatReference[]>;
  unread: number;
  pinned: boolean;
  bindProject(
    projectId: string | null,
    selection?: SelectChatServiceOptions,
  ): Promise<void>;
  selectConversation(conversationId: string): Promise<void>;
  newConversation(): Promise<void>;
  reloadMessages(conversationId?: string): Promise<void>;
  setDraft(text: string): void;
  addRef(ref: ChatReference): void;
  removeRef(refId: string): void;
  send(): Promise<void>;
  retryUnknownSend(): Promise<void>;
  stop(): Promise<void>;
  retry(turnId: string): Promise<void>;
  adjustDraft(
    draftId: string,
    patch: { instruction?: string; goal?: string; target_ids?: string[] },
  ): Promise<void>;
  draftFromMessage(messageId: string, taskKind: TaskKind): Promise<void>;
  createDraft(taskKind: TaskKind): Promise<void>;
  abandonDraft(draftId: string): Promise<void>;
  startDraft(draftId: string): Promise<void>;
  acceptCandidate(messageId: string): Promise<void>;
  cancelTask(jobRef: string): Promise<void>;
  retryTask(jobRef: string): Promise<void>;
  applyEvent(envelope: ChatEventEnvelope): void;
  setConnection(status: ChatConnectionStatus, detail?: string): void;
  clearError(): void;
  resetUnread(): void;
  setPinned(pinned: boolean): void;
}

let unsubscribe: (() => void) | null = null;
let bindGeneration = 0;
let draftTimer: ReturnType<typeof setTimeout> | null = null;
let lastConnection: ChatConnectionStatus = "closed";

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const isServerRejection = (e: unknown) => e instanceof V2Error;

export const useChatStore = create<ChatStore>()((set, get) => {
  /** 更新指定会话视图并返回新视图。 */
  function patchView(
    conversationId: string,
    patch: Partial<ConversationView> | ((v: ConversationView) => Partial<ConversationView>),
  ): ConversationView {
    const current = get().views[conversationId] ?? emptyView();
    const delta = typeof patch === "function" ? patch(current) : patch;
    const updated = { ...current, ...delta };
    set((s) => ({ views: { ...s.views, [conversationId]: updated } }));
    return updated;
  }

  function subscribeActive(projectId: string, conversationId: string) {
    const service = get().service;
    if (!service) return;
    if (unsubscribe) unsubscribe();
    lastConnection = "connecting";
    unsubscribe = service.subscribe(
      projectId,
      conversationId,
      (envelope) => get().applyEvent(envelope),
      (status, detail) => {
        const previous = lastConnection;
        lastConnection = status;
        get().setConnection(status, detail);
        // 重连成功：以服务端为准补偿断线期间的内容（不重放模型调用）。
        if (status === "open" && previous === "closed")
          void get().reloadMessages(conversationId);
      },
    );
  }

  function taskScope(kind: TaskKind) {
    const state = useV2Store.getState();
    const cid = get().activeConversationId;
    const refs = cid ? get().refs[cid] ?? [] : [];
    const beats = state.storyArtifacts.filter((a) =>
      ["narrative.beat", "story.plot_beat"].includes(a.kind));
    const target_ids = kind === "chapter_draft" ? beats.map((a) => a.artifact_id)
      : refs.length ? refs.map((r) => r.target_id)
      : kind === "check" ? [...state.storyArtifacts.map((a) => a.artifact_id),
        ...state.chapters.map((c) => c.chapter_id)] : [];
    return { goal: kind === "chapter_draft"
      ? state.chapter.chapterId ?? `${state.currentId}:manuscript-${crypto.randomUUID()}` : "",
      target_ids, refs };
  }

  return {
    serviceKind: mockEnabled() ? "simulator" : "probing",
    capabilityDetail: "",
    loadError: null,
    protocol: null,
    service: mockEnabled() ? simulator : null,
    projectId: null,
    conversationsByProject: {},
    activeConversationId: null,
    views: {},
    drafts: loadDrafts(),
    refs: {},
    unread: 0,
    pinned: true,

    bindProject: async (projectId, selectionOptions) => {
      const epoch = ++bindGeneration;
      if (unsubscribe) {
        unsubscribe();
        unsubscribe = null;
      }
      if (!projectId) {
        set({
          projectId: null,
          activeConversationId: null,
          serviceKind: "probing",
          service: null,
          capabilityDetail: "",
        });
        return;
      }
      set({ projectId, activeConversationId: null, capabilityDetail: "", loadError: null });
      const selection = await selectChatService(projectId, selectionOptions);
      if (epoch !== bindGeneration) return;
      set({
        serviceKind: selection.kind,
        service: selection.service,
        protocol: selection.protocol,
        capabilityDetail: selection.detail,
      });
      if (!selection.service) return;
      try {
        let conversations = await selection.service.listConversations(projectId);
        if (epoch !== bindGeneration) return;
        if (conversations.length === 0) {
          const created = await selection.service.createConversation(projectId);
          if (epoch !== bindGeneration) return;
          conversations = [created];
        }
        set((s) => ({
          conversationsByProject: { ...s.conversationsByProject, [projectId]: conversations },
        }));
        const first = conversations[0];
        if (first) await get().selectConversation(first.conversation_id);
      } catch (e) {
        if (epoch !== bindGeneration) return;
        set({
          loadError: `会话读取失败：${errText(e)}。检查后端连接后重试；不会显示空列表假装成功。`,
        });
      }
    },

    selectConversation: async (conversationId) => {
      const { service, projectId } = get();
      if (!service || !projectId) return;
      set((s) => ({
        activeConversationId: conversationId,
        views: {
          ...s.views,
          [conversationId]: {
            ...(s.views[conversationId] ?? emptyView()),
            loading: true,
            error: null,
          },
        },
      }));
      try {
        const messages = await service.loadMessages(projectId, conversationId);
        const view = patchView(conversationId, { loading: false, messages });
        set((s) => ({
          unread: s.pinned ? 0 : s.unread,
          views: { ...s.views, [conversationId]: { ...view, error: null } },
        }));
        subscribeActive(projectId, conversationId);
      } catch (e) {
        patchView(conversationId, {
          loading: false,
          error: `会话读取失败：${errText(e)}。检查后端连接后重试。`,
        });
      }
    },

    newConversation: async () => {
      const { service, projectId } = get();
      if (!service || !projectId) return;
      try {
        const conversation = await service.createConversation(projectId);
        set((s) => ({
          conversationsByProject: {
            ...s.conversationsByProject,
            [projectId]: [...(s.conversationsByProject[projectId] ?? []), conversation],
          },
          views: { ...s.views, [conversation.conversation_id]: emptyView() },
        }));
        await get().selectConversation(conversation.conversation_id);
      } catch (e) {
        set({ capabilityDetail: `无法新建会话：${errText(e)}` });
      }
    },

    reloadMessages: async (conversationId) => {
      const { service, projectId } = get();
      const cid = conversationId ?? get().activeConversationId;
      if (!service || !projectId || !cid) return;
      try {
        const server = await service.loadMessages(projectId, cid);
        patchView(cid, (view) => {
          // 仍有本地流式回复时保留该条（服务端尚未落盘完整正文），其余以服务端为准。
          const streaming = view.messages.filter((m) => m.status === "streaming");
          const merged = [
            ...server.filter((m) => !streaming.some((s) => s.message_id === m.message_id)),
            ...streaming,
          ];
          return { messages: merged };
        });
      } catch {
        /* 补偿失败不改已有内容，等下一次重连 */
      }
    },

    setDraft: (text) => {
      const cid = get().activeConversationId;
      if (!cid) return;
      set((s) => ({ drafts: { ...s.drafts, [cid]: text } }));
      if (draftTimer) clearTimeout(draftTimer);
      draftTimer = setTimeout(() => {
        draftTimer = null;
        saveDrafts(get().drafts);
      }, 300);
    },

    addRef: (ref) => {
      const cid = get().activeConversationId;
      if (!cid) return;
      set((s) => {
        const current = s.refs[cid] ?? [];
        if (current.some((r) => r.ref_id === ref.ref_id)) return {};
        return { refs: { ...s.refs, [cid]: [...current, ref] } };
      });
    },

    removeRef: (refId) => {
      const cid = get().activeConversationId;
      if (!cid) return;
      set((s) => ({
        refs: { ...s.refs, [cid]: (s.refs[cid] ?? []).filter((r) => r.ref_id !== refId) },
      }));
    },

    send: async () => {
      const { service, projectId, activeConversationId, drafts, refs } = get();
      if (!service || !projectId || !activeConversationId) return;
      const view = get().views[activeConversationId] ?? emptyView();
      if (view.activeTurnId) return;
      const text = (drafts[activeConversationId] ?? "").trim();
      if (!text) return;
      const wasPending = view.pending;
      const input = {
        text,
        refs: refs[activeConversationId] ?? [],
        idempotency_key: wasPending?.idempotency_key ?? crypto.randomUUID(),
      };
      patchView(activeConversationId, { error: null, pending: null });
      try {
        const { turn, user_message } = await service.sendTurn(
          projectId,
          activeConversationId,
          input,
        );
        patchView(activeConversationId, (v) => ({
          messages: [...v.messages, user_message],
          turns: { ...v.turns, [turn.turn_id]: turn },
          activeTurnId: turn.turn_id,
        }));
        set((s) => ({
          refs: { ...s.refs, [activeConversationId]: [] },
          drafts: { ...s.drafts, [activeConversationId]: "" },
        }));
        if (draftTimer) clearTimeout(draftTimer);
        draftTimer = setTimeout(() => {
          draftTimer = null;
          saveDrafts(get().drafts);
        }, 300);
      } catch (e) {
        if (isServerRejection(e)) {
          // 服务端明确拒绝：草稿保留，作者可直接改后重发。
          patchView(activeConversationId, { error: errText(e) });
          return;
        }
        // 网络故障：结果未知，保留幂等键与草稿，提示用同一请求重试。
        patchView(activeConversationId, {
          pending: {
            idempotency_key: input.idempotency_key,
            text,
            refs: input.refs,
            error: `发送结果未知：${errText(e)}。重试会用同一请求，服务端若已受理则返回同一轮次，不会重复生成。`,
          },
        });
      }
    },

    retryUnknownSend: async () => {
      const cid = get().activeConversationId;
      if (!cid) return;
      const pending = (get().views[cid] ?? emptyView()).pending;
      if (!pending) return;
      // 用原幂等键重发：服务端幂等，客户端不生成新轮次请求。
      set((s) => ({ drafts: { ...s.drafts, [cid]: pending.text } }));
      await get().send();
    },

    stop: async () => {
      const { service, projectId, activeConversationId } = get();
      const view = activeConversationId ? get().views[activeConversationId] : null;
      if (!service || !projectId || !activeConversationId || !view?.activeTurnId) return;
      try {
        await service.cancelTurn(projectId, activeConversationId, view.activeTurnId);
      } catch (e) {
        patchView(activeConversationId, { error: errText(e) });
      }
    },

    retry: async (turnId) => {
      const { service, projectId, activeConversationId } = get();
      if (!service || !projectId || !activeConversationId) return;
      try {
        const turn = await service.retryTurn(projectId, activeConversationId, turnId);
        patchView(activeConversationId, (v) => ({
          turns: { ...v.turns, [turn.turn_id]: turn },
          activeTurnId: turn.turn_id,
          error: null,
        }));
      } catch (e) {
        patchView(activeConversationId, { error: errText(e) });
      }
    },

    adjustDraft: async (draftId, patch) => {
      const { service, projectId, activeConversationId } = get();
      if (!service || !projectId || !activeConversationId) return;
      const view = get().views[activeConversationId] ?? emptyView();
      const message = view.messages.find(
        (m) => m.kind === "task_draft" && (m.payload as TaskDraftPayload)?.draft_id === draftId,
      );
      const payload = message?.payload as TaskDraftPayload | undefined;
      if (!payload) return;
      try {
        const updated = await service.adjustDraft(
          projectId,
          activeConversationId,
          draftId,
          payload.revision,
          patch,
        );
        patchView(activeConversationId, (v) => ({
          messages: v.messages.map((m) =>
            m.message_id === message!.message_id ? { ...m, payload: updated } : m,
          ),
          error: null,
        }));
      } catch (e) {
        patchView(activeConversationId, { error: errText(e) });
      }
    },

    draftFromMessage: async (messageId, taskKind) => {
      const { service, projectId, activeConversationId } = get();
      if (!service || !projectId || !activeConversationId) return;
      try {
        await service.draftFromMessage(
          projectId,
          activeConversationId,
          messageId,
          taskKind,
          taskScope(taskKind),
        );
      } catch (e) {
        patchView(activeConversationId, { error: errText(e) });
      }
    },

    createDraft: async (taskKind) => {
      const { service, projectId, activeConversationId, drafts } = get();
      if (!service || !projectId || !activeConversationId) return;
      try {
        await service.createDraft(
          projectId,
          activeConversationId,
          taskKind,
          (drafts[activeConversationId] ?? "").trim(),
          taskScope(taskKind),
        );
        set((s) => ({ drafts: { ...s.drafts, [activeConversationId]: "" } }));
      } catch (e) {
        patchView(activeConversationId, { error: errText(e) });
      }
    },

    abandonDraft: async (draftId) => {
      const { service, projectId, activeConversationId } = get();
      if (!service || !projectId || !activeConversationId) return;
      try {
        await service.abandonDraft(projectId, activeConversationId, draftId);
        patchView(activeConversationId, (v) => ({
          abandoned: { ...v.abandoned, [draftId]: true },
        }));
      } catch (e) {
        patchView(activeConversationId, { error: errText(e) });
      }
    },

    startDraft: async (draftId) => {
      const { service, projectId, activeConversationId } = get();
      if (!service || !projectId || !activeConversationId) return;
      const view = get().views[activeConversationId] ?? emptyView();
      const message = view.messages.find(
        (m) => m.kind === "task_draft" && (m.payload as TaskDraftPayload)?.draft_id === draftId,
      );
      const payload = message?.payload as TaskDraftPayload | undefined;
      if (!payload) return;
      try {
        await service.startDraft(projectId, activeConversationId, draftId, payload.revision);
      } catch (e) {
        patchView(activeConversationId, { error: errText(e) });
      }
    },

    acceptCandidate: async (messageId) => {
      const { service, projectId, activeConversationId } = get();
      if (!service || !projectId || !activeConversationId) return;
      const v2 = useV2Store.getState();
      try {
        if (isDirty(v2.chapter) || v2.chapter.saving)
          throw new Error("当前正文有未保存稿，请先保存后再接受候选。");
        await service.acceptCandidate(
          projectId,
          activeConversationId,
          messageId,
          v2.head ?? v2.storyCommit,
        );
        await Promise.all([v2.refreshJobs(), v2.refreshManuscript(), v2.refreshStory()]);
      } catch (e) {
        patchView(activeConversationId, { error: errText(e) });
      }
    },

    cancelTask: async (jobRef) => {
      const { service, projectId, activeConversationId } = get();
      if (!service || !projectId || !activeConversationId) return;
      try {
        await service.cancelTask(projectId, activeConversationId, jobRef);
      } catch (e) {
        patchView(activeConversationId, { error: errText(e) });
      }
    },

    retryTask: async (jobRef) => {
      const { service, projectId, activeConversationId } = get();
      if (!service || !projectId || !activeConversationId) return;
      try {
        await service.retryTask(projectId, activeConversationId, jobRef);
      } catch (e) {
        patchView(activeConversationId, { error: errText(e) });
      }
    },

    applyEvent: (envelope) => {
      const s = get();
      // 跨项目晚到事件不污染当前界面。
      if (envelope.project_id !== s.projectId) return;
      // 只为已加载（已订阅）的会话应用事件：切走后再到的迟到事件不建新视图，
      // 切回时以服务端读取为准补齐（§4.1 刷新优先读服务端终态）。
      const view = s.views[envelope.conversation_id];
      if (!view) return;
      const updated = applyChatEvent(view, envelope);
      const grew =
        updated.messages.length > view.messages.length ||
        (updated.messages.at(-1)?.text.length ?? 0) > (view.messages.at(-1)?.text.length ?? 0);
      set((state) => ({
        views: { ...state.views, [envelope.conversation_id]: updated },
        unread: grew && !state.pinned ? state.unread + 1 : state.unread,
      }));
    },

    setConnection: (status, detail) => {
      const cid = get().activeConversationId;
      if (!cid) return;
      patchView(cid, { connection: status, connectionDetail: detail ?? null });
    },

    clearError: () => {
      const cid = get().activeConversationId;
      if (!cid) return;
      patchView(cid, { error: null });
    },

    resetUnread: () => set({ unread: 0 }),

    setPinned: (pinned) => set({ pinned, unread: pinned ? 0 : get().unread }),
  };
});

// ---------------------------------------------------------------------------
// 选择器（组件只读这些派生值；空值用稳定常量，避免快照死循环）
// ---------------------------------------------------------------------------

export const activeView = (s: ChatStore): ConversationView | null =>
  s.activeConversationId ? (s.views[s.activeConversationId] ?? null) : null;

export const activeMessages = (s: ChatStore): ChatMessage[] =>
  activeView(s)?.messages ?? NO_MESSAGES;

export const activeTurns = (s: ChatStore): Record<string, ChatTurn> =>
  activeView(s)?.turns ?? NO_TURNS;

export const activeAbandoned = (s: ChatStore): Record<string, boolean> =>
  activeView(s)?.abandoned ?? NO_ABANDONED;

export const activeTurnId = (s: ChatStore): string | null =>
  activeView(s)?.activeTurnId ?? null;

export const activeRefs = (s: ChatStore): ChatReference[] =>
  s.activeConversationId ? (s.refs[s.activeConversationId] ?? NO_REFS) : NO_REFS;

export const activeDraft = (s: ChatStore): string =>
  s.activeConversationId ? (s.drafts[s.activeConversationId] ?? "") : "";

export const activeConversations = (s: ChatStore): ChatConversation[] =>
  s.projectId ? (s.conversationsByProject[s.projectId] ?? NO_CONVERSATIONS) : NO_CONVERSATIONS;
