/**
 * 讨论焦点的草稿状态。
 *
 * 点击「讨论调整」只准备引用：这里记下这一轮要看的对象、章节或选段，打开对话，
 * 不建会话、不发请求、不调用模型。真正发送时才把焦点冻结进请求；发送失败保留，
 * 发送成功后清空。
 */

import { create } from "zustand";

import type { DiscussionFocusInput } from "../api/workspace";
import { draftKeyOf, useConversationsStore } from "./conversations";
import { useLayoutStore } from "./layout";

export interface DiscussionFocusEntry {
  focus: DiscussionFocusInput;
  /** 输入区显示的短标签；不代表服务端对象已经存在。 */
  label: string;
}

interface DiscussionFocusState {
  /** 每个草稿槽位一份焦点：切换会话时不把引用带到另一段讨论。 */
  entries: Record<string, DiscussionFocusEntry>;
  prepare(entry: DiscussionFocusEntry): void;
  /** 发送成功后按当时槽位清空；切到另一段讨论不误删新焦点。 */
  clear(key?: string): void;
}

function currentKey(): string {
  return draftKeyOf(useConversationsStore.getState());
}

export const useDiscussionFocusStore = create<DiscussionFocusState>()((set) => ({
  entries: {},

  prepare(entry) {
    const key = currentKey();
    set((state) => ({ entries: { ...state.entries, [key]: entry } }));
    useLayoutStore.getState().revealChat();
  },

  clear(key = currentKey()) {
    set((state) => {
      if (!state.entries[key]) return state;
      const entries = { ...state.entries };
      delete entries[key];
      return { entries };
    });
  },
}));

/** 当前草稿槽位的焦点；没有时返回 null。 */
export function currentDiscussionFocus(
  state: DiscussionFocusState,
): DiscussionFocusEntry | null {
  return state.entries[draftKeyOf(useConversationsStore.getState())] ?? null;
}
