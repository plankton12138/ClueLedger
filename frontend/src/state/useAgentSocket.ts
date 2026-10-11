/**
 * 连接生命周期 hook（P3 T8，P4 T6 多工作区）：slug 变更即重连——先关旧连接
 * 再连新 slug，快照「重置后重放」语义消化工作区切换的状态更替（计划 D5）。
 * 重连是显式动作（reconnect 返回给 UI），不做自动重连风暴。
 */

import { useCallback, useEffect, useRef } from "react";
import { AgentSocket, wsUrl } from "../api/socket";
import type { CommandType } from "../api/types";
import { useChatStore } from "./store";

export function useAgentSocket(): {
  reconnect(): void;
  sendCommand(type: CommandType, message?: string, chapter?: string): Promise<void>;
} {
  const socketRef = useRef<AgentSocket | null>(null);
  const slug = useChatStore((s) => s.slug);

  const connect = useCallback((workspaceSlug: string) => {
    useChatStore.getState().setConnectionError(null);
    socketRef.current?.close();
    const socket = new AgentSocket(wsUrl(workspaceSlug), {
      onStatus: (status) => useChatStore.getState().setStatus(status),
      onEvent: (event) => useChatStore.getState().handleEvent(event),
      // 对账通知（P5 T5）：驱动 toolbar 徽标与对话流同步卡片
      onNotice: (notice) => useChatStore.getState().applyNotice(notice),
      // token 用量帧（P10 T12，D11）：驱动对话头上下文占用显示
      onTokenUsage: (usage) => useChatStore.getState().applyTokenUsage(usage),
      onSnapshot: (state, messages) => useChatStore.getState().loadSnapshot(state, messages),
      onError: (message) => useChatStore.getState().setConnectionError(message),
    });
    socket.connect();
    socketRef.current = socket;
  }, []);

  useEffect(() => {
    if (slug === null) return;
    connect(slug);
    return () => {
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [connect, slug]);

  const sendCommand = useCallback(
    async (type: CommandType, message?: string, chapter?: string) => {
      const socket = socketRef.current;
      if (socket === null)
        throw new Error("连接未初始化。怎样算对：等页面加载完成或点击重连后重试。");
      await socket.command(type, message, chapter);
    },
    [],
  );

  return { reconnect: () => { if (slug !== null) connect(slug); }, sendCommand };
}
