/**
 * Zustand 全局 store（P3 T8）：reducer 纯函数 + 连接层状态的薄壳。
 * 事件进 → applyEvent 出；快照进 → applyMessages 出——store 自身零业务逻辑，
 * （前端不跑任何 Agent 逻辑，只做"事件的另一个消费者"）。
 */

import { create } from "zustand";
import type { AgentStateSnapshot, ConnectionStatus } from "../api/socket";
import type { AgentMessageWire, EventFrame } from "../api/types";
import { applyEvent, applyMessages, initialUiState, type UiState } from "./reducer";

interface ChatStore extends UiState {
  /** 当前工作区 slug（P4 T6）；null = 尚未选定（启动引导/新建第一个工作区）。 */
  slug: string | null;
  workspaces: string[];
  status: ConnectionStatus;
  connectionError: string | null;
  snapshot: AgentStateSnapshot | null;
  setSlug(slug: string): void;
  setWorkspaces(slugs: string[]): void;
  setStatus(status: ConnectionStatus): void;
  setConnectionError(message: string | null): void;
  handleEvent(event: EventFrame): void;
  loadSnapshot(state: AgentStateSnapshot, messages: AgentMessageWire[]): void;
}

export const useChatStore = create<ChatStore>()((set) => ({
  ...initialUiState,
  slug: null,
  workspaces: [],
  status: "closed",
  connectionError: null,
  snapshot: null,
  // 切换工作区：slug 变更驱动 useAgentSocket 重连；旧工作区的事件帧/条目
  // 由 loadSnapshot 的「重置后重放」语义在新快照到达时整体更替（计划 D5）。
  setSlug: (slug) => set({ slug }),
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
    })),
}));
