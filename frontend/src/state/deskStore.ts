/**
 * 主工作区壳 store（M01/V03-1.1 壳层接线）——`state/desk.ts` 纯函数的
 * zustand 薄包装，不复制逻辑、不新增不变量。
 *
 * - 主区唯一变更入口 `navigate`；数据事件只进数据区；
 * - `projectId` 由壳层在项目切换时同步，跨作品数据事件由
 *   `applyOperationResult` 丢弃；
 * - `focusMode`（专注写作收起两侧）持久化，模式同 ChatSidebar 的 OPEN_KEY；
 * - `entryReceipts` 记录 save_idea 回执（V03-1.2）：回执不是 OperationEnvelope
 *   （无 operation_id），不伪造冻结 DTO，单独成列与操作结果同区展示。
 */

import { create } from "zustand";
import type { OperationEnvelope } from "../api/identities";
import {
  applyOperationResult,
  emptyDeskState,
  navigate as navigateView,
  saveReadingPosition as savePosition,
  type DeskState,
  type MainView,
  type ReadingPosition,
} from "./desk";

const FOCUS_KEY = "hastings.desk.focus";

function readFocus(): boolean {
  try {
    return localStorage.getItem(FOCUS_KEY) === "1";
  } catch {
    return false;
  }
}

function writeFocus(on: boolean): void {
  try {
    localStorage.setItem(FOCUS_KEY, on ? "1" : "0");
  } catch {
    /* 禁用存储时仅本次会话生效。 */
  }
}

/** save_idea 回执（POST /api/v2/entries 响应的 camelCase 消费形态）。 */
export interface EntryReceipt {
  entryId: string;
  projectId: string;
  conversationId: string | null;
  messageId: string;
  /** 想法原文（列表展示与接着讨论按钮上下文）。 */
  text: string;
  status: string;
  savedAt: string;
}

interface DeskStore extends DeskState {
  focusMode: boolean;
  entryReceipts: EntryReceipt[];
  /** 最近一次作者操作（导入/修改完成/保存）：右侧展示后续状态（V03-2.6）。 */
  lastOperationId: string | null;
  /** 接着讨论成功后请求输入框重新聚焦（恢复原输入位置，M02）。 */
  composerFocusTick: number;
  /**
   * 「加入讨论」待插入引用（V03-6.4）：tick 递增触发唯一输入框追加可见引用。
   * 只带一段文字，不夹带领域写入；输入框仍是想法落盘前的草稿。
   */
  composerInsert: { text: string; tick: number } | null;
  setProject(projectId: string | null): void;
  navigate(view: MainView): void;
  applyOperationResult(envelope: OperationEnvelope): void;
  saveReadingPosition(position: ReadingPosition): void;
  addEntryReceipt(receipt: EntryReceipt): void;
  setLastOperation(operationId: string | null): void;
  requestComposerFocus(): void;
  requestComposerInsert(reference: string): void;
  toggleFocus(): void;
}

export const useDeskStore = create<DeskStore>()((set, get) => ({
  ...emptyDeskState(),
  focusMode: readFocus(),
  entryReceipts: [],
  lastOperationId: null,
  composerFocusTick: 0,
  composerInsert: null,
  setProject: (projectId) => {
    if (get().projectId === projectId) return;
    // 换项目：壳层回到未定焦点；旧项目的操作与回执不带入（M01 验收第 5 条）。
    set({ ...emptyDeskState(projectId), entryReceipts: [], lastOperationId: null });
  },
  navigate: (view) => set(navigateView(get(), view)),
  applyOperationResult: (envelope) => set(applyOperationResult(get(), envelope)),
  saveReadingPosition: (position) => set(savePosition(get(), position)),
  addEntryReceipt: (receipt) =>
    set((s) => ({ entryReceipts: [...s.entryReceipts, receipt] })),
  setLastOperation: (operationId) => set({ lastOperationId: operationId }),
  requestComposerFocus: () =>
    set((s) => ({ composerFocusTick: s.composerFocusTick + 1 })),
  requestComposerInsert: (reference) =>
    set((s) => ({
      composerInsert: { text: reference, tick: (s.composerInsert?.tick ?? 0) + 1 },
    })),
  toggleFocus: () =>
    set((s) => {
      const focusMode = !s.focusMode;
      writeFocus(focusMode);
      return { focusMode };
    }),
}));
