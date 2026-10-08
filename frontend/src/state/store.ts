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
  status: ConnectionStatus;
  connectionError: string | null;
  snapshot: AgentStateSnapshot | null;
  setStatus(status: ConnectionStatus): void;
  setConnectionError(message: string | null): void;
  handleEvent(event: EventFrame): void;
  loadSnapshot(state: AgentStateSnapshot, messages: AgentMessageWire[]): void;
}

export const useChatStore = create<ChatStore>()((set) => ({
  ...initialUiState,
  status: "closed",
  connectionError: null,
  snapshot: null,
  setStatus: (status) => set({ status }),
  setConnectionError: (connectionError) => set({ connectionError }),
  handleEvent: (event) => set((s) => applyEvent(s, event)),
  loadSnapshot: (state, messages) =>
    set((s) => ({
      ...applyMessages(s, messages),
      snapshot: state,
      // 页面加载时 agent 可能已在写作中——快照的 is_running 是唯一事实来源
      running: s.running || state.is_running,
    })),
}));
