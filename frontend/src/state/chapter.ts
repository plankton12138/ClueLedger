/**
 * 章节文本纯函数（P5 T1）：标题解析、分段、字数。
 * 服务端 write_chapter 落盘格式是 SSOT——首行 `# 第N章 标题`，空行分段；
 * 本模块只做呈现侧解析，不改写文本（T2 的 diff hunks 也在服务端计算）。
 */

export interface ParsedChapter {
  /** 首行 `#` 后的标题（无标题行时 null，呈现为文件名）。 */
  title: string | null;
  /** 去掉首行标题后的正文（保留原始换行）。 */
  body: string;
}

/** 首行 `#\s*第N章\s*(.*)` → 标题；不匹配则整体视为正文。 */
export function parseChapterHeading(content: string): ParsedChapter {
  const [first, ...rest] = content.split("\n");
  const match = first !== undefined ? /^#\s*第.+?章\s*(.*)$/.exec(first) : null;
  if (match === null) {
    return { title: null, body: content };
  }
  const title = match[1]?.trim() ?? "";
  return { title: title === "" ? null : title, body: rest.join("\n") };
}

/** 空行分段（服务端 diff 同款规约）：连续非空行为一段。 */
export function splitParagraphs(body: string): string[] {
  return body
    .split(/\n\s*\n/)
    .map((p) => p.replace(/\n/g, "").trim())
    .filter((p) => p !== "");
}

/** 字数 = 非空白字符数（CJK 场景比「词数」更贴近作者直觉）。 */
export function countChars(text: string): number {
  return text.replace(/\s/g, "").length;
}
