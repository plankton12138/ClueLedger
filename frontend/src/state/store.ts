/**
 * Zustand 全局 store（P3 T8）：reducer 纯函数 + 连接层状态的薄壳。
 * 事件进 → applyEvent 出；快照进 → applyMessages 出——store 自身零业务逻辑，
 * （前端不跑任何 Agent 逻辑，只做"事件的另一个消费者"）。
 */

import { create } from "zustand";
import type { AgentStateSnapshot, ConnectionStatus } from "../api/socket";
import type { AgentMessageWire, EventFrame } from "../api/types";
import { applyEvent, applyMessages, initialUiState, type UiState } from "./reducer";

/**
 * manuscript 切片（P5 T1，D7 的阅读态子集；T5 扩展 draft/saveState/syncState/diff）。
 * content 是 REST 读回的 baseline；竞态防护在 chapterLoaded——晚到的旧章响应
 * 不得覆盖已切换的新章（按 id 比对后丢弃）。
 */
export interface ManuscriptState {
  chapterIds: string[];
  chapterId: string | null;
  content: string | null;
  loading: boolean;
  error: string | null;
}

export const initialManuscript: ManuscriptState = {
  chapterIds: [],
  chapterId: null,
  content: null,
  loading: false,
  error: null,
};

interface ChatStore extends UiState {
  /** 当前工作区 slug（P4 T6）；null = 尚未选定（启动引导/新建第一个工作区）。 */
  slug: string | null;
  workspaces: string[];
  status: ConnectionStatus;
  connectionError: string | null;
  snapshot: AgentStateSnapshot | null;
  manuscript: ManuscriptState;
  setSlug(slug: string): void;
  setWorkspaces(slugs: string[]): void;
  setStatus(status: ConnectionStatus): void;
  setConnectionError(message: string | null): void;
  handleEvent(event: EventFrame): void;
  loadSnapshot(state: AgentStateSnapshot, messages: AgentMessageWire[]): void;
  setChapters(ids: string[]): void;
  selectChapter(id: string): void;
  chapterLoaded(id: string, content: string): void;
  chapterFailed(id: string, message: string): void;
  resetManuscript(): void;
}

export const useChatStore = create<ChatStore>()((set) => ({
  ...initialUiState,
  slug: null,
  workspaces: [],
  status: "closed",
  connectionError: null,
  snapshot: null,
  manuscript: initialManuscript,
  // 切换工作区：slug 变更驱动 useAgentSocket 重连；旧工作区的事件帧/条目
  // 由 loadSnapshot 的「重置后重放」语义在新快照到达时整体更替（计划 D5）。
  // manuscript 随切换立即重置（P5 D7：工作区切换不残留上一案的章节草稿）。
  setSlug: (slug) =>
    set((s) => ({ slug, manuscript: s.slug === slug ? s.manuscript : initialManuscript })),
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
      // manuscript 不随 loadSnapshot 重置（CP1 修正）：重连/重放是同一工作区的
      // 会话状态更替，正文与未保存草稿必须保留（断线重连丢草稿不可接受）；
      // 重置点唯一在 setSlug（换工作区）。重连后的章节列表新鲜度由
      // ManuscriptPanel 对连接状态的 refetch 负责。
    })),
  setChapters: (chapterIds) => set((s) => ({ manuscript: { ...s.manuscript, chapterIds } })),
  selectChapter: (id) =>
    set((s) => ({
      manuscript: { ...s.manuscript, chapterId: id, content: null, loading: true, error: null },
    })),
  chapterLoaded: (id, content) =>
    set((s) =>
      s.manuscript.chapterId !== id
        ? s // 竞态防护：响应返回前用户已切走 → 丢弃
        : { manuscript: { ...s.manuscript, content, loading: false, error: null } },
    ),
  chapterFailed: (id, message) =>
    set((s) =>
      s.manuscript.chapterId !== id
        ? s
        : { manuscript: { ...s.manuscript, loading: false, error: message } },
    ),
  resetManuscript: () => set({ manuscript: initialManuscript }),
}));
