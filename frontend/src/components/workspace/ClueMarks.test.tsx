import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { ClueMark } from "../../api/clueMarks";
import { clueMarksPlugin, clueRanges } from "./ClueMarks";

function mark(text: string, clueId: string, title: string, quote: string): ClueMark {
  const index = text.indexOf(quote);
  const start = Array.from(text.slice(0, index)).length;
  return {
    clue_id: clueId,
    title,
    start,
    end: start + Array.from(quote).length,
    quote,
    kind: "truth",
    mode: "action",
    chapter: 1,
    sources: ["binding"],
  };
}

function markup(text: string, marks: ClueMark[]) {
  return renderToStaticMarkup(
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[[clueMarksPlugin, { text, marks }]]}
      skipHtml
    >
      {text}
    </ReactMarkdown>,
  );
}

describe("clue mark ranges", () => {
  it("converts codepoint offsets and merges overlapping clues", () => {
    const text = "🔑有人经过时木板没有响。";
    const first = mark(text, "board", "没有响的木板", "木板没有响");
    const second: ClueMark = { ...first, clue_id: "corridor", title: "走廊的声音", start: first.start, end: first.end };
    expect(clueRanges(text, [first, second])).toEqual([
      expect.objectContaining({
        clueIds: ["board", "corridor"],
        titles: ["没有响的木板", "走廊的声音"],
      }),
    ]);
    const html = markup(text, [first, second]);
    expect(html).toContain('data-clue-id="board"');
    expect(html).toContain('data-clue-ids="board corridor"');
    expect(html).toContain(">木板没有响</span>");
    expect(html).toContain("🔑有人经过时");
  });

  it("wraps multiple non-overlapping marks and keeps markdown emphasis", () => {
    const text = "先说**背面内容**，再说灰色鞋底。";
    const marks = [
      mark(text, "back", "背面", "背面内容"),
      mark(text, "shoe", "鞋底", "灰色鞋底"),
    ];
    const html = markup(text, marks);
    expect(html.match(/class="clue-mark[^"]*"/g)).toHaveLength(2);
    expect(html).toContain('data-clue-id="back"');
    expect(html).toContain('data-clue-id="shoe"');
  });

  it("skips a wrong or out-of-range quote instead of guessing", () => {
    const text = "木板没有响。";
    const wrong: ClueMark = {
      clue_id: "board",
      title: "木板",
      start: 0,
      end: 3,
      quote: "另一个句子",
      kind: "truth",
      mode: "action",
      chapter: 1,
      sources: ["binding"],
    };
    expect(clueRanges(text, [wrong])).toEqual([]);
    expect(markup(text, [wrong])).not.toContain("clue-mark");
  });
});
