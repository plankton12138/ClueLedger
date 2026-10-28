import { create } from "zustand";
import { fetchSourceLocation, type SourceLocation } from "../api/clues";
import { errorMessage } from "../api/errors";
import { navigate, pageHash, type WorkspacePage } from "./navigation";
import { useWorkspaceStore } from "./workspace";
import { usePagePositions } from "./pagePositions";
import { useWorkspaceModulesStore } from "./workspaceModules";

interface SourceState {
  workspaceId: string | null;
  evidenceId: string | null;
  asOf: number | null;
  taskChapter: number | null;
  location: SourceLocation | null;
  loading: boolean;
  error: string | null;
  epoch: number;
  returnPage: WorkspacePage | null;
  returnPath: string | null;
  returnOffset: number;
  restoreEpoch: number;
  returnModule: ReturnType<typeof useWorkspaceModulesStore.getState>["selected"];
  restorePosition: { key: string; offset: number; epoch: number } | null;
  consumeRestore(epoch: number): void;
  capture(page: WorkspacePage, taskChapter: number): void;
  load(workspaceId: string, evidenceId: string, asOf: number | null, taskChapter: number): Promise<void>;
  clear(): void;
  back(): Promise<void>;
}

export const useSourceLocationStore = create<SourceState>()((set, get) => ({
  workspaceId: null, evidenceId: null, asOf: null, taskChapter: null, location: null,
  loading: false, error: null, epoch: 0, returnPage: null, returnPath: null, returnOffset: 0, restoreEpoch: 0, returnModule: null, restorePosition: null,
  consumeRestore(epoch) { if (get().restorePosition?.epoch === epoch) set({ restorePosition: null }); },
  capture(page, taskChapter) {
    const path = useWorkspaceStore.getState().selectedPath;
    const module = useWorkspaceModulesStore.getState().selected;
    set({ returnPage: page, returnPath: path, taskChapter, returnModule: module?.workspaceId === page.workspaceId ? module : null, returnOffset: usePagePositions.getState().offsets[`${page.workspaceId}:paper:${path ?? ""}`] ?? 0 });
  },
  async load(workspaceId, evidenceId, asOf, taskChapter) {
    const initial = useWorkspaceStore.getState();
    const epoch = get().epoch + 1;
    set({ workspaceId, evidenceId, asOf, taskChapter, epoch, location: null, loading: true, error: null });
    const current = () => get().epoch === epoch && useWorkspaceStore.getState().currentId === workspaceId && useWorkspaceStore.getState().workspaceEpoch === initial.workspaceEpoch;
    try {
      const location = await fetchSourceLocation(workspaceId, evidenceId, asOf);
      if (!current()) return;
      // 作者在来源请求期间另选了文件：迟到定位不接管阅读区。
      if (useWorkspaceStore.getState().fileEpoch !== initial.fileEpoch) { get().clear(); return; }
      set({ location });
      if (location.source.kind === "file" && location.readable) {
        await useWorkspaceStore.getState().openPath(location.source.path);
        if (!current()) return;
        const currentWorkspace = useWorkspaceStore.getState();
        const file = currentWorkspace.file;
        if (currentWorkspace.error) {
          set({ location: { ...location, freshness: "unavailable", readable: false, start: null, end: null } });
        } else if (file?.path !== location.source.path || file.digest !== location.current_digest) {
          set({ location: { ...location, freshness: file?.path === location.source.path ? "stale" : "source_missing", start: null, end: null } });
        }
      }
    } catch (error) { if (current()) set({ error: errorMessage(error, "原文定位") }); }
    finally { if (current()) set({ loading: false }); }
  },
  clear() { set({ workspaceId: null, evidenceId: null, location: null, loading: false, error: null, epoch: get().epoch + 1, taskChapter: null, asOf: null, returnPage: null, returnPath: null, returnModule: null }); },
  async back() {
    const { returnPage, returnPath, returnOffset, returnModule, workspaceId, evidenceId } = get();
    const hash = typeof window === "undefined" ? "" : window.location.hash;
    const target = returnPage ?? (workspaceId ? { kind: "writing" as const, workspaceId } : null);
    get().clear();
    if (!target || useWorkspaceStore.getState().currentId !== target.workspaceId) return;
    if (returnPath) usePagePositions.getState().offset(`${target.workspaceId}:paper:${returnPath}`, returnOffset);
    // 关闭定位后，导航的写作入口恢复普通阅读，不能再次打开刚关闭的来源链接。
    usePagePositions.getState().remember(target.kind === "materials" ? { kind: "writing", workspaceId: target.workspaceId } : target);
    const restoreEpoch = get().restoreEpoch + 1;
    set({ restoreEpoch, restorePosition: returnPath ? { key: `${target.workspaceId}:paper:${returnPath}`, offset: returnOffset, epoch: restoreEpoch } : null });
    const restoreFile = returnPath ? useWorkspaceStore.getState().openPath(returnPath) : Promise.resolve();
    if (returnModule) useWorkspaceModulesStore.getState().select(returnModule.workspaceId, returnModule.bindingId, returnModule.anchor);
    await restoreFile;
    if (useWorkspaceStore.getState().currentId !== target.workspaceId) return;
    if (window.location.hash !== hash) return;
    navigate(target);
    requestAnimationFrame(() => {
      // 返回页可能仍在隐藏或重新加载中，等实际按钮可见后再恢复焦点。
      const stop = () => { observer.disconnect(); window.removeEventListener("hashchange", check); };
      const check = () => {
        if (window.location.hash !== pageHash(target) || useWorkspaceStore.getState().currentId !== target.workspaceId) { stop(); return; }
        const button = Array.from(document.querySelectorAll<HTMLButtonElement>(`[data-source-evidence="${CSS.escape(evidenceId ?? "")}"]`))
          .find(node => node.getClientRects().length > 0 && !node.disabled);
        if (button) { button.focus({ preventScroll: true }); stop(); }
      };
      const observer = new MutationObserver(check);
      observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden", "class", "style"] });
      window.addEventListener("hashchange", check);
      check();
    });
  },
}));

/** 只有来源阅读期间冻结任务边界；浏览截止章不参与此计算。 */
export function sourceTaskChapter(workspaceId: string | null): number | undefined {
  const state = useSourceLocationStore.getState();
  return state.workspaceId === workspaceId && state.evidenceId && state.taskChapter !== null ? state.taskChapter : undefined;
}
