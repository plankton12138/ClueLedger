/**
 * 主页面与故事资料位置的 hash 路由（0.7）。
 *
 * 静态资源托管与桌面入口都没有服务器页面路由，所以页面位置放在 hash 里：
 * `#/workspaces/{id}/writing`、`#/workspaces/{id}/materials/{section}?chapter=8&entry=...`。
 * 解析器只接受登记过的作品、枚举内的资料页与整数章号；URI 解码失败按无效位置处理，
 * 不把 URL 当成读取本机任意路径的指令。
 */

import { useMemo, useSyncExternalStore } from "react";

export type MaterialSection = "chapters" | "characters" | "timeline" | "clues" | "glossary";

export const MATERIAL_SECTIONS: { key: MaterialSection; label: string }[] = [
  { key: "chapters", label: "章节概览" },
  { key: "characters", label: "人物" },
  { key: "timeline", label: "时间线" },
  { key: "clues", label: "线索与伏笔" },
  { key: "glossary", label: "名词表" },
];

export type WorkspacePage =
  | { kind: "writing"; workspaceId: string }
  | {
      kind: "materials";
      workspaceId: string;
      section: MaterialSection;
      chapter: number | null;
      entryId?: string;
    };

const WORKSPACE_ID = /^[A-Za-z0-9_-]{1,64}$/;
const ENTRY_ID = /^[A-Za-z0-9_-]{1,64}$/;
const SECTIONS = new Set<string>(MATERIAL_SECTIONS.map((item) => item.key));

function sectionOf(value: string): MaterialSection | null {
  return SECTIONS.has(value) ? (value as MaterialSection) : null;
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
  const section = sectionOf(parts[3]);
  if (section === null) return null;
  const params = new URLSearchParams(query);
  const chapterRaw = params.get("chapter");
  let chapter: number | null = null;
  if (chapterRaw !== null) {
    const value = Number(chapterRaw);
    if (!Number.isInteger(value) || value < 0) return null;
    chapter = value;
  }
  const entryRaw = params.get("entry");
  if (entryRaw !== null && !ENTRY_ID.test(entryRaw)) return null;
  const page: WorkspacePage = { kind: "materials", workspaceId, section, chapter };
  if (entryRaw) page.entryId = entryRaw;
  return page;
}

export function pageHash(page: WorkspacePage): string {
  if (page.kind === "writing") return `#/workspaces/${page.workspaceId}/writing`;
  const params = new URLSearchParams();
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
