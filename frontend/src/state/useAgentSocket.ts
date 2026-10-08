/**
 * 连接生命周期 hook（P3 T8）：挂载即连、卸载即断；回调解耦到 zustand store。
 * 重连是显式动作（reconnect 返回给 UI），不做自动重连风暴。
 */

import { useCallback, useEffect, useRef } from "react";
import { AgentSocket, wsUrl } from "../api/socket";
import type { CommandType } from "../api/types";
import { useChatStore } from "./store";

export function useAgentSocket(): {
  reconnect(): void;
  sendCommand(type: CommandType, message?: string): Promise<void>;
} {
  const socketRef = useRef<AgentSocket | null>(null);

  const connect = useCallback(() => {
    useChatStore.getState().setConnectionError(null);
    socketRef.current?.close();
    const socket = new AgentSocket(wsUrl(), {
      onStatus: (status) => useChatStore.getState().setStatus(status),
      onEvent: (event) => useChatStore.getState().handleEvent(event),
      onSnapshot: (state, messages) => useChatStore.getState().loadSnapshot(state, messages),
      onError: (message) => useChatStore.getState().setConnectionError(message),
    });
    socket.connect();
    socketRef.current = socket;
  }, []);

  useEffect(() => {
    connect();
    return () => {
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [connect]);

  const sendCommand = useCallback(async (type: CommandType, message?: string) => {
    const socket = socketRef.current;
    if (socket === null) throw new Error("连接未初始化。怎样算对：等页面加载完成或点击重连后重试。");
    await socket.command(type, message);
  }, []);

  return { reconnect: connect, sendCommand };
}
