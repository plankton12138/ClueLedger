/** 原文选段到 Unicode 码点区间；与服务端 offset_unit=unicode_codepoint 对齐。 */
export interface SelectedRange {
  start: number;
  end: number;
  quote: string;
}

export function codePointLength(text: string): number {
  return Array.from(text).length;
}

export function selectionOffsets(text: string, prefix: string, quote: string): SelectedRange | null {
  if (!quote) return null;
  const points = Array.from(text);
  const start = codePointLength(prefix);
  const end = start + codePointLength(quote);
  if (end > points.length || points.slice(start, end).join("") !== quote) return null;
  return { start, end, quote };
}

/** 从 `<pre>` 内的实际选区取码点区间；渲染文本必须与来源文本一致。 */
export function selectedRange(host: HTMLElement): SelectedRange | null {
  const selection = typeof window === "undefined" ? null : window.getSelection();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null;
  const range = selection.getRangeAt(0);
  if (!host.contains(range.commonAncestorContainer)) return null;
  const prefix = document.createRange();
  prefix.selectNodeContents(host);
  prefix.setEnd(range.startContainer, range.startOffset);
  return selectionOffsets(host.textContent ?? "", prefix.toString(), range.toString());
}
