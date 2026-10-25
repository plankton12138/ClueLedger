import { create } from "zustand";
import type { WorkspacePage } from "./navigation";
interface PagePositions {
  pages: Record<string, Partial<Record<WorkspacePage["kind"], WorkspacePage>>>;
  offsets: Record<string, number>;
  queries: Record<string, string>;
  remember(page: WorkspacePage): void;
  offset(key: string, value: number): void;
  query(key: string, value: string): void;
}
/** 只保留导航位置与滚动；原文和内容草稿归各自领域状态。 */
export const usePagePositions = create<PagePositions>()((set) => ({
  pages: {}, offsets: {}, queries: {},
  remember(page) { set(state => ({ pages: { ...state.pages, [page.workspaceId]: { ...state.pages[page.workspaceId], [page.kind]: page } } })); },
  offset(key, value) { set(state => ({ offsets: { ...state.offsets, [key]: value } })); },
  query(key, value) { set(state => ({ queries: { ...state.queries, [key]: value } })); },
}));
