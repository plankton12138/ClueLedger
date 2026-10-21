/**
 * 对话滚动（V05-2C）。
 *
 * 跟随与阅读是两件事：作者在底部附近时跟着新内容走；主动向上滚、正在选文字或展开
 * 旧内容时停下，只提示「回到最新」。收起思考、展开工具、图片替代文字变长都不该把
 * 正在阅读的人拽回底部，所以这里只在内容真的增长时决定要不要跟随。
 */

import { useCallback, useLayoutEffect, useRef, useState } from "react";

//: 距底部多远算「在底部附近」；窄对话栏走查后的小范围余量。
const BOTTOM_MARGIN = 48;

function selectionInside(element: HTMLElement | null): boolean {
  if (!element) return false;
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed) return false;
  if (!selection.toString().trim()) return false;
  const anchor = selection.anchorNode;
  return anchor ? element.contains(anchor) : false;
}

export interface ChatScroll {
  ref: React.MutableRefObject<HTMLDivElement | null>;
  hasNew: boolean;
  onScroll: () => void;
  toLatest: () => void;
}

/**
 * @param token 内容版本：每收到一条事件或整轮结束时变化。
 * @param active 是否还有一轮在进行；收尾时用来重新判断是否跟随。
 */
export function useChatScroll(token: number): ChatScroll {
  const ref = useRef<HTMLDivElement | null>(null);
  const following = useRef(true);
  const [hasNew, setHasNew] = useState(false);

  const onScroll = useCallback(() => {
    const element = ref.current;
    if (!element) return;
    const near =
      element.scrollHeight - element.scrollTop - element.clientHeight <= BOTTOM_MARGIN;
    following.current = near;
    // 作者主动离开底部就给出「回到最新」入口：不必等到下一条内容到来。
    setHasNew(!near);
  }, []);

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    if (!following.current) {
      setHasNew(true);
      return;
    }
    if (selectionInside(element)) return;
    element.scrollTop = element.scrollHeight;
    setHasNew(false);
  }, [token]);

  const toLatest = useCallback(() => {
    const element = ref.current;
    if (!element) return;
    following.current = true;
    element.scrollTop = element.scrollHeight;
    setHasNew(false);
  }, []);

  return { ref, hasNew, onScroll, toLatest };
}
