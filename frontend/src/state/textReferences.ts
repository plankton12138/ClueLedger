import type { TextReference } from "../api/workspace";
export type { TextReference } from "../api/workspace";

export const MAX_TEXT_REFERENCES = 10;
export const MAX_REFERENCE_TEXT = 20_000;

export function sameTextReferences(left: TextReference[] = [], right: TextReference[] = []): boolean {
  return left.length === right.length && left.every((item, index) => item.id === right[index].id);
}

/** 与作者要求一同提交，因此历史回读、压缩和失败重试都沿用同一份选段。 */
export function withTextReferences(text: string, references: TextReference[]): string {
  if (!references.length) return text;
  return `${text}\n\n---\n\n引用选段（选中时的文本快照）：\n\n${references.map(item => {
    const source = `${item.title} · ${item.source} · 版本 ${item.version}`.replace(/[\r\n]/g, " ");
    return `来源：${source}\n\n${item.text.split("\n").map(line => `> ${line}`).join("\n")}`;
  }).join("\n\n")}`;
}
