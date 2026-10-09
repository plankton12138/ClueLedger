/**
 * Zustand 全局 store（P3 T8）：reducer 纯函数 + 连接层状态的薄壳。
 * 事件进 → applyEvent 出；快照进 → applyMessages 出——store 自身零业务逻辑，
 * （前端不跑任何 Agent 逻辑，只做"事件的另一个消费者"）。
 */

import { create } from "zustand";
import type { DiffHunkWire } from "../api/rest";
import type { AgentStateSnapshot, ConnectionStatus } from "../api/socket";
import type { AgentMessageWire, EventFrame, NoticeFrame } from "../api/types";
import { applyEvent, applyMessages, initialUiState, reportUnhandled, type UiState } from "./reducer";

export type ManuscriptMode = "read" | "edit" | "diff";
export type SaveState = "clean" | "dirty" | "saving" | "error";
export type SyncState = "idle" | "queued" | "syncing" | "done" | "failed";

/** 一则对账完成卡片（对话流呈现，notice 驱动，P5 T5）。 */
export interface SyncCardEntry {
  chapter: string;
  hunks: number;
  updates: number;
  status: "done" | "failed";
}

/**
 * manuscript 切片（P5 D7）：正文成为页面中心后的编辑状态机。
 * baseline 是 REST 读回的已保存版本；draft 是编辑缓冲（dirty = draft !== baseline）；
 * revision 为乐观并发垫底；saveState/syncState/diff 驱动工具栏徽标与对账视图。
 * 竞态防护在 chapterLoaded——晚到的旧章响应不得覆盖已切换的新章（按 id 比对后丢弃）。
 */
export interface ManuscriptState {
  chapterIds: string[];
  chapterId: string | null;
  baseline: string | null;
  draft: string | null;
  revision: number | null;
  mode: ManuscriptMode;
  saveState: SaveState;
  syncState: SyncState;
  diff: DiffHunkWire[] | null;
  loading: boolean;
  error: string | null;
}

export const initialManuscript: ManuscriptState = {
  chapterIds: [],
  chapterId: null,
  baseline: null,
  draft: null,
  revision: null,
  mode: "read",
  saveState: "clean",
  syncState: "idle",
  diff: null,
  loading: false,
  error: null,
};

/** 选中/载入章节时复位编辑态（保留列表中已有章节 id 由调用方另行设置）。 */
function freshManuscript(ms: ManuscriptState, patch: Partial<ManuscriptState>): ManuscriptState {
  return { ...ms, ...patch };
}

interface ChatStore extends UiState {
  /** 当前工作区 slug（P4 T6）；null = 尚未选定（启动引导/新建第一个工作区）。 */
  slug: string | null;
  workspaces: string[];
  status: ConnectionStatus;
  connectionError: string | null;
  snapshot: AgentStateSnapshot | null;
  manuscript: ManuscriptState;
  /** 对账完成卡片（append-only；随快照重放清空）。 */
  syncCards: SyncCardEntry[];
  setSlug(slug: string): void;
  setWorkspaces(slugs: string[]): void;
  setStatus(status: ConnectionStatus): void;
  setConnectionError(message: string | null): void;
  handleEvent(event: EventFrame): void;
  loadSnapshot(state: AgentStateSnapshot, messages: AgentMessageWire[]): void;
  setChapters(ids: string[]): void;
  selectChapter(id: string): void;
  chapterLoaded(id: string, content: string, revision: number): void;
  chapterFailed(id: string, message: string): void;
  setMode(mode: ManuscriptMode): void;
  setDraft(text: string): void;
  setManuscriptError(message: string): void;
  saveStart(): void;
  saveFailed(message: string): void;
  applySaved(revision: number, diff: DiffHunkWire[], savedText: string): void;
  discardDraft(): void;
  applyNotice(notice: NoticeFrame): void;
  resetManuscript(): void;
}

/** notice 序列 → 目标 syncState（queued → syncing → done/failed）；契约外 kind → null。 */
function nextSyncState(notice: NoticeFrame): SyncState | null {
  switch (notice.kind) {
    case "sync_queued":
      return "queued";
    case "sync_start":
      return "syncing";
    case "sync_end":
      return notice.status === "failed" ? "failed" : "done";
    default:
      return null; // 协议演进出的新 kind：交给 unhandled 上报，不静默吞
  }
}

export const useChatStore = create<ChatStore>()((set) => ({
  ...initialUiState,
  slug: null,
  workspaces: [],
  status: "closed",
  connectionError: null,
  snapshot: null,
  manuscript: initialManuscript,
  syncCards: [],
  // 切换工作区：slug 变更驱动 useAgentSocket 重连；旧工作区的事件帧/条目
  // 由 loadSnapshot 的「重置后重放」语义在新快照到达时整体更替（计划 D5）。
  // manuscript 随切换立即重置（P5 D7：工作区切换不残留上一案的章节草稿）。
  setSlug: (slug) =>
    set((s) =>
      s.slug === slug
        ? { slug }
        : { slug, manuscript: initialManuscript, syncCards: [] },
    ),
  setWorkspaces: (workspaces) => set({ workspaces }),
  setStatus: (status) => set({ status }),
  setConnectionError: (connectionError) => set({ connectionError }),
  handleEvent: (event) => set((s) => applyEvent(s, event)),
  loadSnapshot: (state, messages) =>
    set((s) => ({
      // 快照是全量事实：重置到初态后重放（R3 问题1——重连时追加会把历史渲染两遍）；
      // 快照拉取与实时事件之间仅毫秒级窗口，P3 单用户可接受。契约外事件类型报告
      // 跨连接保留（调试视图的证据不随重连丢失）。
      ...applyMessages(
        { ...initialUiState, unhandledEventTypes: s.unhandledEventTypes },
        messages,
      ),
      snapshot: state,
      // 快照的 is_running 是唯一事实来源（R3 问题2——stale true 不得经 || 短路覆盖）
      running: state.is_running,
      // 对账卡片属对话流叙事，随快照重放一并清空（与 items 同生命周期）
      syncCards: [],
      // manuscript 不随 loadSnapshot 重置（CP1 修正）：重连/重放是同一工作区的
      // 会话状态更替，正文与未保存草稿必须保留（断线重连丢草稿不可接受）；
      // 重置点唯一在 setSlug（换工作区）。重连后的章节列表新鲜度由
      // ManuscriptPanel 对连接状态的 refetch 负责。
    })),
  setChapters: (chapterIds) => set((s) => ({ manuscript: { ...s.manuscript, chapterIds } })),
  selectChapter: (id) =>
    set((s) => ({
      manuscript: freshManuscript(s.manuscript, {
        chapterId: id,
        baseline: null,
        draft: null,
        revision: null,
        mode: "read",
        saveState: "clean",
        syncState: "idle",
        diff: null,
        loading: true,
        error: null,
      }),
    })),
  chapterLoaded: (id, content, revision) =>
    set((s) =>
      s.manuscript.chapterId !== id
        ? s // 竞态防护：响应返回前用户已切走 → 丢弃
        : {
            manuscript: freshManuscript(s.manuscript, {
              baseline: content,
              draft: content,
              revision,
              mode: "read",
              saveState: "clean",
              syncState: "idle",
              diff: null,
              loading: false,
              error: null,
            }),
          },
    ),
  chapterFailed: (id, message) =>
    set((s) =>
      s.manuscript.chapterId !== id
        ? s
        : { manuscript: { ...s.manuscript, loading: false, error: message } },
    ),
  setMode: (mode) => set((s) => ({ manuscript: { ...s.manuscript, mode } })),
  setDraft: (text) =>
    set((s) => ({
      manuscript: {
        ...s.manuscript,
        draft: text,
        // dirty 是派生量：草稿与 baseline 一致即回 clean（放弃/手动改回均适用）
        saveState: text === s.manuscript.baseline ? "clean" : "dirty",
      },
    })),
  // 章节列表等非「当前章正文」的错误：不经 chapterFailed 的 id 竞态守卫
  // （R3-③——未选章时 id 为 null，守卫会把它整条吞掉）
  setManuscriptError: (message) =>
    set((s) => ({ manuscript: { ...s.manuscript, error: message } })),
  saveStart: () =>
    set((s) => ({ manuscript: { ...s.manuscript, saveState: "saving", error: null } })),
  saveFailed: (message) =>
    set((s) => ({ manuscript: { ...s.manuscript, saveState: "error", error: message } })),
  applySaved: (revision, diff, savedText) =>
    set((s) => ({
      manuscript: freshManuscript(s.manuscript, {
        // baseline 是「本次提交的文本」，不是当前 draft（R3-②）：晚到的保存响应
        // 不得把保存窗口内的新输入追认为已落盘；UI 侧同时以 readOnly 冻结输入。
        baseline: savedText,
        revision,
        saveState: "clean",
        diff: diff.length > 0 ? diff : null,
        mode: diff.length > 0 ? "diff" : "read",
        error: null,
      }),
    })),
  discardDraft: () =>
    set((s) => ({
      manuscript: freshManuscript(s.manuscript, {
        draft: s.manuscript.baseline,
        saveState: "clean",
        diff: null,
        mode: "read",
        error: null,
      }),
    })),
  applyNotice: (notice) =>
    set((s) => {
      const syncState = nextSyncState(notice);
      if (syncState === null) {
        // 契约外 notice kind：与 reducer 的未知事件同一条口子，显式上报不静默吞
        return reportUnhandled(s, `notice:${String(notice.kind)}`);
      }
      // 工具栏徽标只反映「当前打开的章」——别章的对账不打扰本篇编辑（卡片仍照记）
      const forCurrent = s.manuscript.chapterId === notice.chapter;
      const manuscript = forCurrent ? { ...s.manuscript, syncState } : s.manuscript;
      if (notice.kind !== "sync_end") return { manuscript };
      const card: SyncCardEntry = {
        chapter: notice.chapter,
        hunks: notice.hunks,
        updates: notice.updates,
        status: notice.status === "failed" ? "failed" : "done",
      };
      return { manuscript, syncCards: [...s.syncCards, card] };
    }),
  resetManuscript: () => set({ manuscript: initialManuscript }),
}));