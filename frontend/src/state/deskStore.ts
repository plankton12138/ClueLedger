/**
 * 主工作区壳 store（M01/V03-1.1 壳层接线）——`state/desk.ts` 纯函数的
 * zustand 薄包装，不复制逻辑、不新增不变量。
 *
 * - 主区唯一变更入口 `navigate`；数据事件只进数据区；
 * - `projectId` 由壳层在项目切换时同步，跨作品数据事件由
 *   `applyOperationResult` 丢弃；
 * - V03-10 M1：移除「写想法」入口后，回执与输入框插桩（entryReceipts /
 *   composerFocusTick / composerInsert）一并删除；对话改用 chat store 编排。
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

interface DeskStore extends DeskState {
  /** 最近一次作者操作（导入/修改完成/保存）：右侧展示后续状态（V03-2.6）。 */
  lastOperationId: string | null;
  setProject(projectId: string | null): void;
  navigate(view: MainView): void;
  applyOperationResult(envelope: OperationEnvelope): void;
  saveReadingPosition(position: ReadingPosition): void;
  setLastOperation(operationId: string | null): void;
}

export const useDeskStore = create<DeskStore>()((set, get) => ({
  ...emptyDeskState(),
  lastOperationId: null,
  setProject: (projectId) => {
    if (get().projectId === projectId) return;
    // 换项目：壳层回到未定焦点；旧项目的操作不带入（M01 验收第 5 条）。
    set({ ...emptyDeskState(projectId), lastOperationId: null });
  },
  navigate: (view) => set(navigateView(get(), view)),
  applyOperationResult: (envelope) => set(applyOperationResult(get(), envelope)),
  saveReadingPosition: (position) => set(savePosition(get(), position)),
  setLastOperation: (operationId) => set({ lastOperationId: operationId }),
}));
