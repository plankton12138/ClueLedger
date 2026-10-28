import type { Root, Element, Text, RootContent } from "hast";
import type { Plugin } from "unified";

export interface SourceRange { start: number; end: number }

/** Python 坐标是码点，Markdown 解析器和 DOM 坐标是 UTF-16。 */
export function sourceRange(text: string, start: number | null, end: number | null, quote: string): SourceRange | null {
  if (start === null || end === null || !Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || end <= start) return null;
  const points = Array.from(text);
  if (end > points.length || points.slice(start, end).join("") !== quote) return null;
  return { start: points.slice(0, start).join("").length, end: points.slice(0, end).join("").length };
}

type Positioned = Root | RootContent;
const BLOCKS = new Set(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "td", "th"]);
interface HighlightOptions { text: string; range: SourceRange | null }
function offsets(node: Positioned): SourceRange | null {
  const start = node.position?.start.offset, end = node.position?.end.offset;
  return start === undefined || end === undefined ? null : { start, end };
}
function intersects(left: SourceRange, right: SourceRange) { return left.start < right.end && right.start < left.end; }

/** 依解析器源位置处理，不搜索渲染文本；实体、转义等无法安全对齐时定位到所在块。 */
export const sourceHighlightPlugin: Plugin<[HighlightOptions], Root> = ({ text, range }) => tree => {
  if (!range) return;
  const blocks: Element[] = [], leaves: Text[] = [];
  function inspect(node: Positioned) {
    const span = offsets(node);
    if (span && intersects(span, range!)) {
      if (node.type === "text") leaves.push(node);
      if (node.type === "element" && BLOCKS.has(node.tagName)) blocks.push(node);
    }
    if ("children" in node) node.children.forEach(inspect);
  }
  inspect(tree);
  const safe = leaves.length > 0 && leaves.every(node => {
    const span = offsets(node)!;
    return text.slice(span.start, span.end) === node.value;
  });
  if (!safe) {
    for (const node of blocks) {
      const span = offsets(node)!;
      if (blocks.some(child => child !== node && offsets(child)!.start >= span.start && offsets(child)!.end <= span.end)) continue;
      node.properties = { ...node.properties, dataSourceTarget: "paragraph", tabIndex: -1, className: ["source-highlight-block"] };
    }
    return;
  }
  function apply(node: Positioned) {
    if (!("children" in node)) return;
    const children: RootContent[] = [];
    for (const child of node.children) {
      const span = offsets(child);
      if (child.type === "text" && span && intersects(span, range!)) {
        const begin = Math.max(range!.start, span.start) - span.start;
        const end = Math.min(range!.end, span.end) - span.start;
        if (begin) children.push({ type: "text", value: child.value.slice(0, begin) });
        children.push({ type: "element", tagName: "mark", properties: { dataSourceTarget: "exact", tabIndex: -1 }, children: [{ type: "text", value: child.value.slice(begin, end) }] });
        if (end < child.value.length) children.push({ type: "text", value: child.value.slice(end) });
      } else { apply(child); children.push(child); }
    }
    node.children = children;
  }
  apply(tree);
};

export function RawSourceHighlight({ text, range }: { text: string; range: SourceRange | null }) {
  return <pre className="source-raw">{range ? <>{text.slice(0, range.start)}<mark data-source-target="exact" tabIndex={-1}>{text.slice(range.start, range.end)}</mark>{text.slice(range.end)}</> : text}</pre>;
}
