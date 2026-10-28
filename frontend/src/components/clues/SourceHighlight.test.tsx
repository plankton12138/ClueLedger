import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { sourceHighlightPlugin, sourceRange, RawSourceHighlight } from "./SourceHighlight";

function markup(text: string, quote: string, last = false) {
  const index = last ? text.lastIndexOf(quote) : text.indexOf(quote);
  const start = Array.from(text.slice(0, index)).length;
  const range = sourceRange(text, start, start + Array.from(quote).length, quote);
  return renderToStaticMarkup(<ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[[sourceHighlightPlugin, { text, range }]]} skipHtml>{text}</ReactMarkdown>);
}

describe("source coordinate highlighting", () => {
  it("converts codepoints and selects the second identical quote after emoji", () => {
    const html = markup("# 标题\n\n🔑同一句。\n\n𠮷又是同一句。", "同一句。", true);
    expect(html.match(/<mark /g)).toHaveLength(1);
    expect(html).toContain("🔑同一句。");
    expect(html).toContain("𠮷又是<mark");
    expect(html).toContain(">同一句。</mark>");
  });
  it("uses source positions inside emphasis and links", () => {
    for (const text of ["先说**背面内容**。", "请看[背面内容](https://example.com)。"])
      expect(markup(text, "背面内容")).toContain(">背面内容</mark>");
  });
  it("falls back to the actual paragraph for entities and escapes", () => {
    for (const [text, quote] of [["第一段。\n\n第二段 &amp; 内容。", "&amp;"], ["第一段。\n\n第二段 \\*星号。", "\\*"], ["第一段。\n\n记下 `背面内容`。", "背面内容"]]) {
      const html = markup(text, quote);
      expect(html).toContain('data-source-target="paragraph"');
      expect(html).not.toContain("<mark");
      expect(html).toContain("<p>第一段。</p>");
    }
  });
  it("rejects wrong, out-of-range and split codepoint coordinates", () => {
    for (const [start, end, quote] of [[0, 1, "错误"], [-1, 2, "🔑字"], [0, 3, "🔑字"], [0.5, 1, "🔑"]] as const)
      expect(sourceRange("🔑字", start, end, quote)).toBeNull();
    expect(sourceRange("🔑字", 1, 2, "字")).toEqual({ start: 2, end: 3 });
  });
  it("raw metadata remains text and highlights exact source without rendering HTML", () => {
    const text = "---\nroles: [suspect]\n---\n<script>🔑</script>";
    const start = Array.from(text.slice(0, text.indexOf("🔑"))).length;
    const html = renderToStaticMarkup(<RawSourceHighlight text={text} range={sourceRange(text, start, start + 1, "🔑")} />);
    expect(html).toContain("roles: [suspect]");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain(">🔑</mark>");
  });
});
