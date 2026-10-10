/**
 * 思考过程折叠卡（P9 T2，D1）：历史/中断条目与流式思考统一默认折叠——
 * 折叠是渲染层改造，reducer 与事件契约零改动。
 *
 * - 历史条目（kind:"thinking"）：折叠态单行「◈ 思路 · N 字 · 展开」，点击展开原文；
 * - 流式 thinkingBuffer：只渲染脉动行「思考中… N 字」，字数随流增长——
 *   流式期也不刷屏，比「结束后再折叠」更强的收敛；
 * - 展开态用会话内 useState，默认折叠是硬行为，不做持久化偏好。
 */

import { useState } from "react";

/** 字数口径：非空白字符数（空白不是思考内容，避免换行/空格把字数撑大）。 */
export function thinkingCharCount(text: string): number {
  return text.replace(/\s+/g, "").length;
}

/** 历史条目折叠态文案。 */
export function thinkingCollapsedLabel(chars: number): string {
  return `◈ 思路 · ${chars} 字 · 展开`;
}

/** 流式期脉动文案：尚无字数时只显示「思考中…」。 */
export function thinkingStreamingLabel(chars: number): string {
  return chars > 0 ? `思考中… ${chars} 字` : "思考中…";
}

export default function ThinkingCard({
  text,
  streaming = false,
}: {
  text: string;
  /** 流式期 = 强制单行脉动（不提供展开，内容尚未定稿）。 */
  streaming?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const chars = thinkingCharCount(text);

  if (streaming) {
    return (
      <div className="max-w-[85%] border-l-2 border-line px-4 py-2">
        <p className="animate-pulse text-xs italic text-gold-soft/60">
          {thinkingStreamingLabel(chars)}
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-[85%] border-l-2 border-line px-4 py-2">
      {open ? (
        <>
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="text-xs italic text-gold-soft/70 hover:text-gold-soft"
          >
            {`◈ 思路 · ${chars} 字 · 收起`}
          </button>
          <p className="mt-1 whitespace-pre-wrap break-words text-xs italic leading-relaxed text-paper/50">
            {text}
          </p>
        </>
      ) : (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="text-xs italic text-gold-soft/70 hover:text-gold-soft"
        >
          {thinkingCollapsedLabel(chars)}
        </button>
      )}
    </div>
  );
}
