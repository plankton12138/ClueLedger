/**
 * 灵感悬浮面板的开关状态（0.8 I2）。
 *
 * 小按钮与面板共享位置，只有位置偏好写入本机存储。expanded 表示搜索/全部灵感，
 * 不改变内容容器；草稿与记录仍在 `state/ideas.ts`。
 */

import { create } from "zustand";

export interface IdeaPosition { x: number; y: number }
export const IDEAS_POSITION_KEY = "hastings.ideas.position";
export const DEFAULT_IDEA_POSITION: IdeaPosition = { x: 1, y: 0.72 };

export function parseIdeaPosition(raw: string | null): IdeaPosition {
  try {
    const value = JSON.parse(raw ?? "null") as IdeaPosition | null;
    if (value && typeof value.x === "number" && Number.isFinite(value.x) && typeof value.y === "number" && Number.isFinite(value.y)) {
      return { x: Math.max(0, Math.min(1, value.x)), y: Math.max(0, Math.min(1, value.y)) };
    }
  } catch { /* 损坏的偏好使用默认位置。 */ }
  return DEFAULT_IDEA_POSITION;
}

function readPosition(): IdeaPosition {
  try { return parseIdeaPosition(localStorage.getItem(IDEAS_POSITION_KEY)); }
  catch { return DEFAULT_IDEA_POSITION; }
}

interface IdeasSurfaceState {
  open: boolean;
  expanded: boolean;
  position: IdeaPosition;
  setPosition(position: IdeaPosition): void;
  resetPosition(): void;
  /** 打开前的触发元素：收起后把焦点还回去。 */
  origin: HTMLElement | null;
  /** 旧链接带过来的条目；定位一次后清空。 */
  entryId: string | null;
  show(options?: { origin?: HTMLElement | null; expanded?: boolean; entryId?: string | null }): void;
  hide(): void;
  toggle(): void;
  setExpanded(expanded: boolean): void;
  consumeEntry(): string | null;
}

export const useIdeasSurfaceStore = create<IdeasSurfaceState>()((set, get) => ({
  open: false,
  expanded: false,
  position: readPosition(),
  setPosition(position) {
    if (!Number.isFinite(position.x) || !Number.isFinite(position.y)) return;
    const next = { x: Math.max(0, Math.min(1, position.x)), y: Math.max(0, Math.min(1, position.y)) };
    set({ position: next });
    try { localStorage.setItem(IDEAS_POSITION_KEY, JSON.stringify(next)); } catch { /* 位置不可持久保存时仍能拖动。 */ }
  },
  resetPosition() { get().setPosition(DEFAULT_IDEA_POSITION); },
  origin: null,
  entryId: null,

  show(options = {}) {
    const active = typeof document === "undefined" ? null : document.activeElement;
    set({
      open: true,
      expanded: options.expanded ?? false,
      origin: options.origin ?? (active instanceof HTMLElement ? active : null),
      entryId: options.entryId ?? null,
    });
  },

  hide() {
    set({ open: false });
    const origin = get().origin;
    if (origin?.isConnected) origin.focus();
  },

  toggle() {
    if (get().open) get().hide();
    else get().show();
  },

  setExpanded(expanded) {
    set({ expanded });
  },

  consumeEntry() {
    const entry = get().entryId;
    if (entry) set({ entryId: null });
    return entry;
  },
}));
