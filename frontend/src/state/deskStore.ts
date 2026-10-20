/**
 * 主工作区壳 store（M01/V03-1.1 壳层接线）——`state/desk.ts` 纯函数的
 * zustand 薄包装，不复制逻辑、不新增不变量。
 *
 * - 主区唯一变更入口 `navigate`；`projectId` 由壳层在项目切换时同步；
 * - V03-10 M1：移除「写想法」入口后，回执与输入框插桩
 *   （entryReceipts / composerFocusTick / composerInsert）一并删除。
 * - V03-10 M7：撤下后续状态 / 操作结果面板后，操作结果缓存（operations /
 *   lastOperationId）失去消费方，一并移除。
 */

import { create } from "zustand";
import {
  emptyDeskState,
  navigate as navigateView,
  saveReadingPosition as savePosition,
  type DeskState,
  type MainView,
  type ReadingPosition,
} from "./desk";

interface DeskStore extends DeskState {
  setProject(projectId: string | null): void;
  navigate(view: MainView | null): void;
  saveReadingPosition(position: ReadingPosition): void;
}

export const useDeskStore = create<DeskStore>()((set, get) => ({
  ...emptyDeskState(),
  setProject: (projectId) => {
    if (get().projectId === projectId) return;
    // 换项目：壳层回到未定焦点；旧项目的操作不带入（M01 验收第 5 条）。
    set({ ...emptyDeskState(projectId) });
  },
  navigate: (view) => set(navigateView(get(), view)),
  saveReadingPosition: (position) => set(savePosition(get(), position)),
}));
