/**
 * 写作台布局偏好（0.4 V04-11）。
 *
 * 对话与目录的显隐、两个栏宽与字号是界面状态，不属于作品数据，也不进后端：它们只影响这一台机器
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
export const FONT_SCALE: WidthRange = { default: 100, min: 80, max: 150 };

export interface LayoutPrefs {
  sidebarWidth: number;
  chatWidth: number;
  chatVisible: boolean;
  sidebarVisible: boolean;
  fontScale: number;
}

export const DEFAULT_LAYOUT: LayoutPrefs = {
  sidebarWidth: SIDEBAR.default,
  chatWidth: CHAT.default,
  chatVisible: true,
  sidebarVisible: true,
  fontScale: FONT_SCALE.default,
};

export interface WritingPlan {
  kind: "three" | "two" | "reading" | "single";
  sidebar: number;
  chat: number;
  paperMin: number;
}
/** 只计算实际显示宽度，不把自动收起或宽度钳制写入长期偏好。 */
export function writingLayout(width: number, prefs: LayoutPrefs): WritingPlan {
  const scale = Math.max(1, prefs.fontScale / 100);
  const preferredSidebar = clampWidth(prefs.sidebarWidth, SIDEBAR);
  const chatMin = CHAT.min * scale;
  const preferredChat = Math.max(chatMin, clampWidth(prefs.chatWidth, CHAT));
  const widePaper = 560 * scale, compactPaper = 420 * scale, handle = 6;
  // 目录能否常驻只看窗口预算，关闭对话不自动展开目录。
  const sidebar = prefs.sidebarVisible && width >= 1280 * scale && width >= preferredSidebar + chatMin + widePaper + handle * 2 ? preferredSidebar : 0;
  if (!prefs.chatVisible) return sidebar
    ? { kind: "reading", sidebar, chat: 0, paperMin: widePaper }
    : { kind: "single", sidebar: 0, chat: 0, paperMin: 0 };
  if (sidebar) {
    return { kind: "three", sidebar, chat: Math.min(preferredChat, width - sidebar - widePaper - handle * 2), paperMin: widePaper };
  }
  if (width >= 900 && width >= chatMin + compactPaper + handle) {
    return { kind: "two", sidebar: 0, chat: Math.min(preferredChat, width - compactPaper - handle), paperMin: compactPaper };
  }
  return { kind: "single", sidebar: 0, chat: 0, paperMin: 0 };
}

/** 栏宽及字号百分比钳制到区间内；非数值按默认值处理。 */
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
      sidebarVisible: typeof parsed.sidebarVisible === "boolean" ? parsed.sidebarVisible : true,
      fontScale: clampWidth(parsed.fontScale, FONT_SCALE),
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
  toggleSidebar(): void;
  setFontScale(scale: number): void;
}

export const useLayoutStore = create<LayoutStore>()((set, get) => ({
  ...readLayout(),

  setFontScale(scale) {
    set({ fontScale: clampWidth(scale, FONT_SCALE) });
    writeLayout(get());
  },

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

  toggleSidebar() {
    set(state => ({ sidebarVisible: !state.sidebarVisible }));
    writeLayout(get());
  },
}));
