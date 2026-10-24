/**
 * 主页面与故事资料位置的 hash 路由（0.8 E）。
 *
 * 静态资源托管与桌面入口都没有服务器页面路由，所以页面位置放在 hash 里：
 * `#/workspaces/{id}/writing`、`#/workspaces/{id}/materials/{section}?order=story&chapter=8`。
 * 解析器只接受登记过的作品、枚举内的资料页与整数章号；URI 解码失败按无效位置处理，
 * 不把 URL 当成读取本机任意路径的指令。
 *
 * 0.8 把「章节概览」与「时间线」合成同一个「章节与事件」页：两段旧路由仍可打开，
 * 分别落到章节顺序与故事顺序，不再维护第三份独立的时间线正文。
 */

import { useMemo, useSyncExternalStore } from "react";

export type StoryOrder = "chapter" | "story";

export type MaterialSection = "story" | "characters" | "clues" | "glossary";

export const MATERIAL_SECTIONS: { key: MaterialSection; label: string }[] = [
  { key: "story", label: "章节与事件" },
  { key: "characters", label: "人物" },
  { key: "clues", label: "线索与伏笔" },
  { key: "glossary", label: "名词表" },
];

/** 旧路由到新位置的映射：保留旧链接可用，但页面只有合并后的一份视图。 */
const LEGACY_SECTIONS: Record<string, { section: MaterialSection; order: StoryOrder }> = {
  chapters: { section: "story", order: "chapter" },
  timeline: { section: "story", order: "story" },
};

export type WorkspacePage =
  | { kind: "writing"; workspaceId: string }
  | {
      kind: "materials";
      workspaceId: string;
      section: MaterialSection;
      chapter: number | null;
      order: StoryOrder;
      entryId?: string;
    };

const WORKSPACE_ID = /^[A-Za-z0-9_-]{1,64}$/;
const ENTRY_ID = /^[A-Za-z0-9_-]{1,64}$/;
const SECTIONS = new Set<string>(MATERIAL_SECTIONS.map((item) => item.key));

function resolveSection(value: string): { section: MaterialSection; order: StoryOrder } | null {
  if (SECTIONS.has(value)) {
    return { section: value as MaterialSection, order: "chapter" };
  }
  return LEGACY_SECTIONS[value] ?? null;
}

export function parseHash(hash: string): WorkspacePage | null {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  const [path, query = ""] = raw.split("?", 2);
  const parts = path.split("/").filter(Boolean);
  if (parts.length < 3 || parts[0] !== "workspaces") return null;
  const workspaceId = parts[1];
  if (!WORKSPACE_ID.test(workspaceId)) return null;
  if (parts[2] === "writing" && parts.length === 3) {
    return { kind: "writing", workspaceId };
  }
  if (parts[2] !== "materials" || parts.length < 4) return null;
  const resolved = resolveSection(parts[3]);
  if (resolved === null) return null;
  const params = new URLSearchParams(query);
  const orderRaw = params.get("order");
  // 显式 order 只用于章节与事件页；旧 chapters/timeline 已带自己的默认顺序。
  const order: StoryOrder =
    resolved.section === "story" && (orderRaw === "story" || orderRaw === "chapter")
      ? orderRaw
      : resolved.order;
  const chapterRaw = params.get("chapter");
  let chapter: number | null = null;
  if (chapterRaw !== null) {
    const value = Number(chapterRaw);
    if (!Number.isInteger(value) || value < 0) return null;
    chapter = value;
  }
  const entryRaw = params.get("entry");
  if (entryRaw !== null && !ENTRY_ID.test(entryRaw)) return null;
  const page: WorkspacePage = { kind: "materials", workspaceId, section: resolved.section, chapter, order };
  if (entryRaw) page.entryId = entryRaw;
  return page;
}

export function pageHash(page: WorkspacePage): string {
  if (page.kind === "writing") return `#/workspaces/${page.workspaceId}/writing`;
  const params = new URLSearchParams();
  if (page.section === "story" && page.order === "story") params.set("order", "story");
  if (page.chapter !== null) params.set("chapter", String(page.chapter));
  if (page.entryId) params.set("entry", page.entryId);
  const suffix = params.toString();
  return `#/workspaces/${page.workspaceId}/materials/${page.section}${suffix ? `?${suffix}` : ""}`;
}

function subscribe(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

function snapshot(): string {
  return typeof window === "undefined" ? "" : window.location.hash;
}

export function useWorkspacePage(): WorkspacePage | null {
  const hash = useSyncExternalStore(subscribe, snapshot, () => "");
  return useMemo(() => parseHash(hash), [hash]);
}

export function navigate(page: WorkspacePage): void {
  if (typeof window === "undefined") return;
  const next = pageHash(page);
  if (window.location.hash !== next) window.location.hash = next;
}
