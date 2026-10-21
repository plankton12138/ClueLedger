/**
 * 对话滚动（V05-2C）。
 *
 * 跟随与阅读是两件事：作者在底部附近时跟着新内容走；主动向上滚、正在选文字或展开
 * 旧内容时停下，只提示「回到最新」。收起思考、展开工具、图片替代文字变长都不该把
 * 正在阅读的人拽回底部，所以这里只在内容真的增长时决定要不要跟随。
 */

import { useCallback, useEffect, useRef, useState } from "react";

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
 * @param token 已合批的内容版本。
 * @param conversationId 切换会话后重新定位，旧会话的回调不能移动新会话。
 */
export function useChatScroll(token: number, conversationId: string | null): ChatScroll {
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

  useEffect(() => {
    following.current = true;
    setHasNew(false);
  }, [conversationId]);

  useEffect(() => {
    // 与浏览器绘制对齐，同一帧最多读取和调整一次布局，不在每条事件的提交阶段强制排版。
    const frame = requestAnimationFrame(() => {
      const element = ref.current;
      if (!element) return;
      if (!following.current || selectionInside(element)) {
        setHasNew(true);
        return;
      }
      element.scrollTop = element.scrollHeight;
      setHasNew(false);
    });
    return () => cancelAnimationFrame(frame);
  }, [token, conversationId]);

  const toLatest = useCallback(() => {
    const element = ref.current;
    if (!element) return;
    following.current = true;
    element.scrollTop = element.scrollHeight;
    setHasNew(false);
  }, []);

  return { ref, hasNew, onScroll, toLatest };
}
