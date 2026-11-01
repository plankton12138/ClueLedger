/**
 * 源坐标锚点：把 Markdown 解析器的源位置带到 DOM，供选区换算。
 *
 * 只在「渲染出的文本与本段源文本完全一致」时加锚点；实体、转义等情况宁可没有锚点，
 * 也不用渲染文字去猜 Markdown 偏移。
 */

import type { Root, RootContent } from "hast";
import type { Plugin } from "unified";

interface Options {
  text: string;
}

type Positioned = Root | RootContent;

function spanOf(node: Positioned): { start: number; end: number } | null {
  const start = node.position?.start.offset;
  const end = node.position?.end.offset;
  return start === undefined || end === undefined ? null : { start, end };
}

export const sourceAnchorsPlugin: Plugin<[Options], Root> = ({ text }) => tree => {
  function apply(node: Positioned) {
    if (!("children" in node)) return;
    const children: RootContent[] = [];
    for (const child of node.children) {
      const span = spanOf(child);
      if (child.type === "text" && span && text.slice(span.start, span.end) === child.value) {
        children.push({
          type: "element",
          tagName: "span",
          properties: {
            dataSrcStart: span.start,
            dataSrcEnd: span.end,
            className: ["source-anchor"],
          },
          children: [child],
        });
        continue;
      }
      apply(child);
      children.push(child);
    }
    node.children = children;
  }
  apply(tree);
};

/** UTF-16 偏移转码点偏移；落在代理对中间时拒绝，不截断半个字符。 */
export function toCodePointOffset(text: string, utf16Offset: number): number {
  if (!Number.isInteger(utf16Offset) || utf16Offset < 0 || utf16Offset > text.length) {
    throw new Error("源坐标越界");
  }
  const before = text.charCodeAt(utf16Offset - 1);
  const after = text.charCodeAt(utf16Offset);
  const splitsPair =
    before >= 0xd800 && before <= 0xdbff && after >= 0xdc00 && after <= 0xdfff;
  if (splitsPair) throw new Error("源坐标落在字符内部");
  return Array.from(text.slice(0, utf16Offset)).length;
}

function anchorOf(node: Node | null): HTMLElement | null {
  const element = node instanceof Element ? node : node?.parentElement ?? null;
  return element?.closest<HTMLElement>("[data-src-start]") ?? null;
}

function offsetInside(anchor: HTMLElement, boundary: Node, boundaryOffset: number): number {
  const range = document.createRange();
  range.selectNodeContents(anchor);
  range.setEnd(boundary, boundaryOffset);
  return range.toString().length;
}

/** 当前选区的源码点区间；无法证明连续源区间时返回 null。 */
export function selectionSourceRange(
  root: HTMLElement,
  sourceText: string,
): { start: number; end: number; quote: string } | null {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
  const range = selection.getRangeAt(0);
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return null;
  const startAnchor = anchorOf(range.startContainer);
  const endAnchor = anchorOf(range.endContainer);
  if (!startAnchor || !endAnchor) return null;
  const startBase = Number(startAnchor.dataset.srcStart);
  const endBase = Number(endAnchor.dataset.srcStart);
  if (!Number.isFinite(startBase) || !Number.isFinite(endBase)) return null;
  const startUtf16 = startBase + offsetInside(startAnchor, range.startContainer, range.startOffset);
  const endUtf16 = endBase + offsetInside(endAnchor, range.endContainer, range.endOffset);
  if (endUtf16 <= startUtf16) return null;
  try {
    const start = toCodePointOffset(sourceText, startUtf16);
    const end = toCodePointOffset(sourceText, endUtf16);
    const points = Array.from(sourceText);
    const quote = points.slice(start, end).join("");
    return quote.trim() ? { start, end, quote } : null;
  } catch {
    return null;
  }
}
