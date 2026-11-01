import type { Root, RootContent } from "hast";
import type { Plugin } from "unified";

import type { ClueMark } from "../../api/clueMarks";
import { sourceRange, type SourceRange } from "../clues/SourceHighlight";

export interface ClueRange extends SourceRange {
  clueIds: string[];
  titles: string[];
}

type Positioned = Root | RootContent;

interface Options {
  text: string;
  marks: ClueMark[];
}

function offsets(node: Positioned): SourceRange | null {
  const start = node.position?.start.offset;
  const end = node.position?.end.offset;
  return start === undefined || end === undefined ? null : { start, end };
}

function intersects(left: SourceRange, right: SourceRange): boolean {
  return left.start < right.end && right.start < left.end;
}

export function clueRanges(text: string, marks: ClueMark[]): ClueRange[] {
  const ranges = marks
    .flatMap((mark) => {
      const range = sourceRange(text, mark.start, mark.end, mark.quote);
      return range
        ? [{ ...range, clueIds: [mark.clue_id], titles: [mark.title] }]
        : [];
    })
    .sort((left, right) => left.start - right.start || right.end - left.end);
  const merged: ClueRange[] = [];
  for (const range of ranges) {
    const last = merged[merged.length - 1];
    if (last && range.start < last.end) {
      last.end = Math.max(last.end, range.end);
      last.clueIds = Array.from(new Set([...last.clueIds, ...range.clueIds]));
      last.titles = Array.from(new Set([...last.titles, ...range.titles]));
    } else {
      merged.push({ ...range, clueIds: [...range.clueIds], titles: [...range.titles] });
    }
  }
  return merged;
}

/** 用 Markdown 解析器的源位置切分文本节点，不搜索渲染后的 DOM。 */
export const clueMarksPlugin: Plugin<[Options], Root> = ({ text, marks }) => (tree) => {
  const ranges = clueRanges(text, marks);
  if (ranges.length === 0) return;

  function apply(node: Positioned) {
    if (!("children" in node)) return;
    const children: RootContent[] = [];
    for (const child of node.children) {
      const span = offsets(child);
      if (child.type === "text" && span && ranges.some((range) => intersects(span, range))) {
        const value = text.slice(span.start, span.end);
        if (value !== child.value) {
          children.push(child);
          continue;
        }
        let cursor = span.start;
        for (const range of ranges) {
          if (!intersects(span, range)) continue;
          const begin = Math.max(span.start, range.start);
          const end = Math.min(span.end, range.end);
          if (begin > cursor) children.push({ type: "text", value: text.slice(cursor, begin) });
          children.push({
            type: "element",
            tagName: "span",
            properties: {
              className: ["clue-mark"],
              dataClueId: range.clueIds[0],
              dataClueIds: range.clueIds.join(" "),
              dataClueTitle: range.titles.join(" · "),
              role: "button",
              tabIndex: 0,
              title: range.titles.join(" · "),
            },
            children: [{ type: "text", value: text.slice(begin, end) }],
          });
          cursor = end;
        }
        if (cursor < span.end) children.push({ type: "text", value: text.slice(cursor, span.end) });
      } else {
        apply(child);
        children.push(child);
      }
    }
    node.children = children;
  }

  apply(tree);
};
