/**
 * 上下文引用桥（§3.4）：页面推荐引用，作者显式添加后才进入本轮请求。
 *
 * - 已保存内容带项目 ID、对象 ID 与 Revision/Commit 版本；
 * - 编辑器未保存内容只在作者明确「引用选中文本」时以一次性摘录附加，标明未保存；
 * - 不做隐式整页注入，切页不悄悄替换已附加引用。
 */

import type { ChatReference } from "../../api/chat";

export interface SelectionOffer {
  chapterId: string;
  chapterTitle: string;
  excerpt: string;
}

type Listener = (offer: SelectionOffer) => void;
const listeners = new Set<Listener>();

/** 创作台编辑器调用：把选中文本作为一次性摘录提供给对话栏。 */
export function offerSelection(offer: SelectionOffer) {
  for (const listener of [...listeners]) listener(offer);
}

/** 对话栏订阅：作者点「引用」菜单中的选中文本项时读取。 */
export function onSelectionOffer(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

let refSeed = 0;

export function chapterRef(
  chapter: { chapter_id: string; title: string; chapter_base: string },
): ChatReference {
  refSeed += 1;
  return {
    ref_id: `chapter:${chapter.chapter_id}`,
    kind: "chapter",
    target_id: chapter.chapter_id,
    title: `当前章 · ${chapter.title || chapter.chapter_id}`,
    version: chapter.chapter_base,
    saved: true,
  };
}

export function artifactRef(artifact: {
  artifact_id: string;
  kind: string;
  content: Record<string, unknown>;
}, version = ""): ChatReference {
  refSeed += 1;
  const title =
    String(artifact.content.name || artifact.content.title || artifact.content.statement || "") ||
    artifact.artifact_id;
  return {
    ref_id: `artifact:${artifact.artifact_id}`,
    kind: "artifact",
    target_id: artifact.artifact_id,
    title,
    version,
    saved: true,
  };
}

export function selectionRef(offer: SelectionOffer): ChatReference {
  refSeed += 1;
  return {
    ref_id: `selection:${offer.chapterId}:${refSeed}`,
    kind: "selection",
    target_id: offer.chapterId,
    title: `选中文本 · ${offer.chapterTitle || offer.chapterId}`,
    version: "",
    saved: false,
    excerpt: offer.excerpt,
  };
}
