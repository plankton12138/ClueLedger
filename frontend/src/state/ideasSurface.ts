/**
 * 灵感悬浮面板的开关状态（0.8 I2）。
 *
 * 面板只有一份：宽屏是非模态侧栏，窄屏是近全屏容器，「展开查看」只是更大的区域。
 * 这里只保存开关、展开、来处焦点与待定位条目；草稿、列表与选中都在 `state/ideas.ts`，
 * 切换容器不会重建内容。
 */

import { create } from "zustand";

interface IdeasSurfaceState {
  open: boolean;
  expanded: boolean;
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
  origin: null,
  entryId: null,

  show(options = {}) {
    const active = typeof document === "undefined" ? null : document.activeElement;
    set({
      open: true,
      expanded: options.expanded ?? get().expanded,
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
