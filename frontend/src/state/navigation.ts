/**
 * 主页面位置的 hash 路由。
 *
 * 静态资源托管与桌面入口都没有服务器页面路由，所以页面位置放在 hash 里：
 * `#/workspaces/{id}/writing`、`#/workspaces/{id}/ideas`。
 * 解析器只接受登记过的作品与整数章号；URI 解码失败按无效位置处理，
 * 不把 URL 当成读取本机任意路径的指令。
 *
 * 故事资料页已移除：旧的 `materials` 链接回落到写作页，不保留兼容页。
 */

import { useMemo, useSyncExternalStore } from "react";

export type WorkspacePage =
  | { kind: "writing"; workspaceId: string; sourceId?: string; clueId?: string; asOfChapter?: number; taskChapter?: number }
  | { kind: "ideas"; workspaceId: string; ideaId?: string };

export type LibraryPage = { kind: "library"; moduleId?: string; revisionId?: string; workspaceContextId?: string };
export type AppPage = WorkspacePage | LibraryPage;

const WORKSPACE_ID = /^[A-Za-z0-9_-]{1,64}$/;
const ENTRY_ID = /^[A-Za-z0-9_-]{1,64}$/;

export function parseHash(hash: string): AppPage | null {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  const [path, query = ""] = raw.split("?", 2);
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "library") {
    const params = new URLSearchParams(query);
    const context = params.get("workspace");
    if (context !== null && !WORKSPACE_ID.test(context)) return null;
    const page: LibraryPage = { kind: "library" };
    if (context) page.workspaceContextId = context;
    if (parts.length === 1) return page;
    if (parts[1] !== "modules" || !ENTRY_ID.test(parts[2] ?? "")) return null;
    page.moduleId = parts[2];
    if (parts.length === 3) return page;
    if (parts.length !== 5 || parts[3] !== "revisions" || !ENTRY_ID.test(parts[4])) return null;
    page.revisionId = parts[4];
    return page;
  }
  if (parts.length < 3 || parts[0] !== "workspaces") return null;
  const workspaceId = parts[1];
  if (!WORKSPACE_ID.test(workspaceId)) return null;
  if (parts[2] === "writing" && parts.length === 3) {
    const params = new URLSearchParams(query);
    const sourceId = params.get("source"), clueId = params.get("clue");
    const asOf = params.get("as_of"), task = params.get("task_chapter");
    if (sourceId !== null && !/^ev_[a-f0-9]{20}_[a-f0-9]{32}$/.test(sourceId)) return null;
    if (clueId !== null && !ENTRY_ID.test(clueId)) return null;
    for (const value of [asOf, task]) if (value !== null && (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)))) return null;
    const page: WorkspacePage = { kind: "writing", workspaceId };
    if (sourceId) page.sourceId = sourceId;
    if (clueId) page.clueId = clueId;
    if (asOf !== null) page.asOfChapter = Number(asOf);
    if (task !== null) page.taskChapter = Number(task);
    return page;
  }
  if (parts[2] === "ideas" && parts.length === 3) {
    const params = new URLSearchParams(query);
    const entryRaw = params.get("entry");
    if (entryRaw !== null && !ENTRY_ID.test(entryRaw)) return null;
    const page: WorkspacePage = { kind: "ideas", workspaceId };
    if (entryRaw) page.ideaId = entryRaw;
    return page;
  }
  // 已移除的总览与故事资料链接返回当前作品写作页：不保留兼容页。
  if (parts[2] === "overview" && parts.length === 3) return { kind: "writing", workspaceId };
  if (parts[2] === "materials") return { kind: "writing", workspaceId };
  return null;
}

export function pageHash(page: AppPage): string {
  if (page.kind === "library") {
    const resource = page.moduleId ? `/modules/${page.moduleId}${page.revisionId ? `/revisions/${page.revisionId}` : ""}` : "";
    return `#/library${resource}${page.workspaceContextId ? `?workspace=${encodeURIComponent(page.workspaceContextId)}` : ""}`;
  }
  if (page.kind === "writing") {
    const params = new URLSearchParams();
    if (page.sourceId) params.set("source", page.sourceId);
    if (page.clueId) params.set("clue", page.clueId);
    if (page.asOfChapter !== undefined) params.set("as_of", String(page.asOfChapter));
    if (page.taskChapter !== undefined) params.set("task_chapter", String(page.taskChapter));
    return `#/workspaces/${page.workspaceId}/writing${params.size ? `?${params}` : ""}`;
  }
  const suffix = page.ideaId ? `?entry=${encodeURIComponent(page.ideaId)}` : "";
  return `#/workspaces/${page.workspaceId}/ideas${suffix}`;
}

function subscribe(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

function snapshot(): string {
  return typeof window === "undefined" ? "" : window.location.hash;
}

export function useWorkspacePage(): AppPage | null {
  const hash = useSyncExternalStore(subscribe, snapshot, () => "");
  return useMemo(() => parseHash(hash), [hash]);
}

export function navigate(page: AppPage, replace = false): void {
  if (typeof window === "undefined") return;
  const next = pageHash(page);
  if (window.location.hash !== next) {
    if (replace) { window.history.replaceState(null, "", next); window.dispatchEvent(new HashChangeEvent("hashchange")); }
    else window.location.hash = next;
  }
}
