/**
 * 修订面板的打开状态（v1.2）。
 *
 * 对话里的修订回执与工具栏都要打开同一个抽屉；把目标放在这里，避免两个入口各自
 * 维护一份开关。`autoAnalyze` 只在从「让模型分析」进入时为真，抽屉消费一次后清掉。
 */

import { create } from "zustand";

export interface RevisionPanelTarget {
  workspaceId: string;
  chapterId: string | null;
  revisionId: string | null;
  tab: "revisions" | "style";
  autoAnalyze: boolean;
  /** 从对话回执进入时带上当前会话，分析记录据此关联。 */
  conversationId: string | null;
}

interface RevisionPanelStore {
  target: RevisionPanelTarget | null;
  open: (
    target: Omit<RevisionPanelTarget, "autoAnalyze" | "conversationId"> & {
      autoAnalyze?: boolean;
      conversationId?: string | null;
    },
  ) => void;
  close: () => void;
}

export const useRevisionPanelStore = create<RevisionPanelStore>(set => ({
  target: null,
  open: input =>
    set({
      target: {
        ...input,
        autoAnalyze: input.autoAnalyze ?? false,
        conversationId: input.conversationId ?? null,
      },
    }),
  close: () => set({ target: null }),
}));
