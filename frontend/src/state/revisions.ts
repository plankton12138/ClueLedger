/**
 * 写作工作区的打开状态（v1.2）。
 *
 * 对话栏用「对话 / 本章修订 / 写作约定」切换视图，回执只负责带入目标。
 * `autoAnalyze` 表示打开时请求分析，已有结果或正在进行的分析由内容区恢复。
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
