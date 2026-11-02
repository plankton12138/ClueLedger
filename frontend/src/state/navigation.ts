/**
 * 主页面位置的 hash 路由。
 *
 * 静态资源托管与桌面入口都没有服务器页面路由，所以页面位置放在 hash 里：
 * `#/workspaces/{id}/writing?chapter=…`、`#/workspaces/{id}/ideas`。
 * 写作页只有一个入口：不带选择时显示全书构思，带 `chapter` 时显示该章，
 * 带 `prose` 时只读打开正文或设定文件。旧 `mode=story / clues / chapter`
 * 链接解析成同一页，由上层把地址栏改写成新形式。
 *
 * 解析器只接受登记过的作品与安全标识；URI 解码失败按无效位置处理，
 * 不把 URL 当成读取本机任意路径的指令。
 */

import { useMemo, useSyncExternalStore } from "react";

export type WorkspacePage =
  | {
      kind: "writing";
      workspaceId: string;
      /** 章节标识；缺省且没有 `prosePath` 时是全书构思。 */
      chapterId?: string;
      /** 只读打开的正文或其他文件；与 `chapterId` 互斥。 */
      prosePath?: string;
      /** 页边索引中选中的信息项；只能跟章节一起出现。 */
      informationId?: string;
      /** 来源定位：账本证据打开时使用。 */
      sourceId?: string;
      asOfChapter?: number;
      taskChapter?: number;
    }
  | { kind: "ideas"; workspaceId: string; ideaId?: string };

export type LibraryPage = { kind: "library"; moduleId?: string; revisionId?: string; workspaceContextId?: string };
export type AppPage = WorkspacePage | LibraryPage;

const WORKSPACE_ID = /^[A-Za-z0-9_-]{1,64}$/;
const ENTRY_ID = /^[A-Za-z0-9_-]{1,64}$/;

/** 文件路径只允许作品内的相对路径：拒绝绝对路径、盘符与上级目录。 */
function safePath(value: string): boolean {
  if (!value || value.length > 512) return false;
  if (value.startsWith("/") || value.includes("\\") || value.includes("\0")) return false;
  if (/^[A-Za-z]:/.test(value)) return false;
  return !value.split("/").some(segment => segment === ".." || segment === ".");
}

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
    const sourceId = params.get("source");
    const asOf = params.get("as_of"), task = params.get("task_chapter");
    // 旧模式只用于识别旧链接；不再进入页面状态，地址栏由调用方改写。
    const mode = params.get("mode");
    if (mode !== null && mode !== "story" && mode !== "clues" && mode !== "chapter") return null;
    const chapterId = params.get("chapter");
    const prosePath = params.get("prose");
    const informationId = params.get("information");
    if (chapterId !== null && !ENTRY_ID.test(chapterId)) return null;
    if (prosePath !== null && !safePath(prosePath)) return null;
    if (informationId !== null && !ENTRY_ID.test(informationId)) return null;
    // 一章或一篇正文只能选一种；信息项只能跟章节一起出现。
    if (chapterId && prosePath) return null;
    if (informationId && !chapterId) return null;
    if (sourceId !== null && !/^ev_[a-f0-9]{20}_[a-f0-9]{32}$/.test(sourceId)) return null;
    for (const value of [asOf, task]) if (value !== null && (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)))) return null;
    const page: WorkspacePage = { kind: "writing", workspaceId };
    if (chapterId) page.chapterId = chapterId;
    if (prosePath) page.prosePath = prosePath;
    if (informationId) page.informationId = informationId;
    if (sourceId) page.sourceId = sourceId;
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
    if (page.chapterId) params.set("chapter", page.chapterId);
    else if (page.prosePath) params.set("prose", page.prosePath);
    if (page.informationId && page.chapterId) params.set("information", page.informationId);
    if (page.sourceId) params.set("source", page.sourceId);
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
