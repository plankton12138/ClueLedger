/**
 * 写作台布局偏好（0.4 V04-11）。
 *
 * 对话栏显隐与两个栏宽是界面状态，不属于作品数据，也不进后端：它们只影响这一台机器
 * 上的窗口怎么排，所以单独放一份状态，并写在浏览器本地存储里，重开窗口保持原样。
 *
 * 读取失败（没有本地存储、内容损坏、数值越界）一律回到默认值，不阻断启动。
 */

import { create } from "zustand";

const STORAGE_KEY = "hastings.workspace.layout";

export interface WidthRange {
  default: number;
  min: number;
  max: number;
}

export const SIDEBAR: WidthRange = { default: 260, min: 180, max: 400 };
export const CHAT: WidthRange = { default: 380, min: 280, max: 560 };

export interface LayoutPrefs {
  sidebarWidth: number;
  chatWidth: number;
  chatVisible: boolean;
}

export const DEFAULT_LAYOUT: LayoutPrefs = {
  sidebarWidth: SIDEBAR.default,
  chatWidth: CHAT.default,
  chatVisible: true,
};

/** 宽度钳制到区间内；非数值（NaN、字符串、缺省）按默认值处理。 */
export function clampWidth(value: unknown, range: WidthRange): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return range.default;
  return Math.min(range.max, Math.max(range.min, Math.round(value)));
}

/** 把存储里的原始字符串解析成可用偏好：任何异常都退回默认值。 */
export function parseLayout(raw: string | null): LayoutPrefs {
  if (!raw) return DEFAULT_LAYOUT;
  try {
    const parsed = JSON.parse(raw) as Partial<LayoutPrefs> | null;
    if (!parsed || typeof parsed !== "object") return DEFAULT_LAYOUT;
    return {
      sidebarWidth: clampWidth(parsed.sidebarWidth, SIDEBAR),
      chatWidth: clampWidth(parsed.chatWidth, CHAT),
      chatVisible: typeof parsed.chatVisible === "boolean" ? parsed.chatVisible : true,
    };
  } catch {
    return DEFAULT_LAYOUT;
  }
}

function readLayout(): LayoutPrefs {
  if (typeof localStorage === "undefined") return DEFAULT_LAYOUT;
  try {
    return parseLayout(localStorage.getItem(STORAGE_KEY));
  } catch {
    return DEFAULT_LAYOUT;
  }
}

function writeLayout(prefs: LayoutPrefs): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // 存储不可写（隐私模式、配额满）时只丢偏好，不影响本次使用
  }
}

interface LayoutStore extends LayoutPrefs {
  setSidebarWidth(width: number): void;
  setChatWidth(width: number): void;
  toggleChat(): void;
}

export const useLayoutStore = create<LayoutStore>()((set, get) => ({
  ...readLayout(),

  setSidebarWidth(width) {
    set({ sidebarWidth: clampWidth(width, SIDEBAR) });
    writeLayout(get());
  },

  setChatWidth(width) {
    set({ chatWidth: clampWidth(width, CHAT) });
    writeLayout(get());
  },

  toggleChat() {
    set((state) => ({ chatVisible: !state.chatVisible }));
    writeLayout(get());
  },
}));
