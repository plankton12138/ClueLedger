/**
 * 修订模式里的即时段落差异（v1.2）。
 *
 * 作者在稿纸上边改边看时，正文还没入稿，服务端没有这次修改的记录，所以差异在浏览器里
 * 按同一套规则计算：段落对齐、段内字符级增删。改稿确认后，修订记录与差异由服务端
 * 重新计算并保存，两边使用相同的展示结构，作者看到的效果一致。
 */

export interface InlineSpan {
  kind: "equal" | "delete" | "insert";
  text: string;
}

export type DiffBlock =
  | { kind: "equal"; text: string }
  | { kind: "replace"; before: string; after: string; inline: InlineSpan[] }
  | { kind: "delete"; before: string }
  | { kind: "insert"; after: string };

/** 与服务端一致：段落内部差异的字符上限，超过时整段标为改写。 */
const INLINE_LIMIT = 4000;
/** 替换块按相似度配对，太不像的两个段落分开显示成删除与新增。 */
const PAIR_RATIO = 0.3;

export function splitParagraphs(text: string): string[] {
  return text
    .replace(/\r\n?/g, "\n")
    .split(/\n[ \t]*\n/)
    .map(block => block.trim())
    .filter(Boolean);
}

function lcsTable(a: string[], b: string[]): Int32Array[] {
  const table = Array.from({ length: a.length + 1 }, () => new Int32Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] = a[i] === b[j]
        ? table[i + 1][j + 1] + 1
        : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  return table;
}

function mergeSpans(spans: InlineSpan[]): InlineSpan[] {
  const merged: InlineSpan[] = [];
  for (const span of spans) {
    if (!span.text) continue;
    const last = merged[merged.length - 1];
    if (last && last.kind === span.kind) last.text += span.text;
    else merged.push({ ...span });
  }
  return merged;
}

/** 段落内部的字符级差异；两侧都过长时退化为整段改写。 */
export function inlineDiff(before: string, after: string): InlineSpan[] {
  const left = Array.from(before);
  const right = Array.from(after);
  if (left.length > INLINE_LIMIT || right.length > INLINE_LIMIT) {
    return mergeSpans([
      { kind: "delete", text: before },
      { kind: "insert", text: after },
    ]);
  }
  const table = lcsTable(left, right);
  const spans: InlineSpan[] = [];
  let i = 0;
  let j = 0;
  while (i < left.length && j < right.length) {
    if (left[i] === right[j]) {
      spans.push({ kind: "equal", text: left[i] });
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      spans.push({ kind: "delete", text: left[i] });
      i += 1;
    } else {
      spans.push({ kind: "insert", text: right[j] });
      j += 1;
    }
  }
  while (i < left.length) {
    spans.push({ kind: "delete", text: left[i] });
    i += 1;
  }
  while (j < right.length) {
    spans.push({ kind: "insert", text: right[j] });
    j += 1;
  }
  return mergeSpans(spans);
}

function similarity(before: string, after: string): number {
  if (!before.length && !after.length) return 1;
  const left = Array.from(before);
  const right = Array.from(after);
  const common = lcsTable(left, right)[0][0];
  return (2 * common) / (left.length + right.length);
}

interface RawOp {
  kind: "equal" | "delete" | "insert";
  text: string;
}

/** 段落级 LCS；连续的非公共块交给配对步骤处理。 */
function rawOps(before: string[], after: string[]): RawOp[] {
  const table = lcsTable(before, after);
  const ops: RawOp[] = [];
  let i = 0;
  let j = 0;
  while (i < before.length && j < after.length) {
    if (before[i] === after[j]) {
      ops.push({ kind: "equal", text: before[i] });
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      ops.push({ kind: "delete", text: before[i] });
      i += 1;
    } else {
      ops.push({ kind: "insert", text: after[j] });
      j += 1;
    }
  }
  while (i < before.length) {
    ops.push({ kind: "delete", text: before[i] });
    i += 1;
  }
  while (j < after.length) {
    ops.push({ kind: "insert", text: after[j] });
    j += 1;
  }
  return ops;
}

export function diffParagraphs(beforeText: string, afterText: string): DiffBlock[] {
  const ops = rawOps(splitParagraphs(beforeText), splitParagraphs(afterText));
  const blocks: DiffBlock[] = [];
  let index = 0;
  while (index < ops.length) {
    const op = ops[index];
    if (op.kind === "equal") {
      blocks.push({ kind: "equal", text: op.text });
      index += 1;
      continue;
    }
    const removed: string[] = [];
    const added: string[] = [];
    while (index < ops.length && ops[index].kind !== "equal") {
      if (ops[index].kind === "delete") removed.push(ops[index].text);
      else added.push(ops[index].text);
      index += 1;
    }
    const shared = Math.min(removed.length, added.length);
    for (let offset = 0; offset < shared; offset += 1) {
      const left = removed[offset];
      const right = added[offset];
      if (similarity(left, right) >= PAIR_RATIO) {
        blocks.push({ kind: "replace", before: left, after: right, inline: inlineDiff(left, right) });
      } else {
        blocks.push({ kind: "delete", before: left });
        blocks.push({ kind: "insert", after: right });
      }
    }
    for (let offset = shared; offset < removed.length; offset += 1) {
      blocks.push({ kind: "delete", before: removed[offset] });
    }
    for (let offset = shared; offset < added.length; offset += 1) {
      blocks.push({ kind: "insert", after: added[offset] });
    }
  }
  return blocks;
}

/** 差异里有多少处段落改动，用于编辑态提示。 */
export function changeCount(blocks: DiffBlock[]): number {
  return blocks.filter(block => block.kind !== "equal").length;
}
