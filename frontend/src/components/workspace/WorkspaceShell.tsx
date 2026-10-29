/** 应用级导航与领域页面装配。页面切换只改变显示，不重建任务或内容草稿。 */
import { useEffect, useRef, useState } from "react";
import { useConversationsStore } from "../../state/conversations";
import { useModuleLibraryStore } from "../../state/moduleLibrary";
import { useModelsStore } from "../../state/models";
import { navigate, parseHash, useWorkspacePage, type WorkspacePage } from "../../state/navigation";
import { useIdeasSurfaceStore } from "../../state/ideasSurface";
import { usePagePositions } from "../../state/pagePositions";
import { useWorkspaceStore } from "../../state/workspace";
import IdeasSurface from "../ideas/IdeasSurface";
import LibraryRoute from "../library/LibraryRoute";
import ModelSettingsDialog from "../models/ModelSettingsDialog";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";
import ActivityPanel from "./ActivityPanel";
import DirectoryDialog from "./DirectoryDialog";
import AppHeader from "./AppHeader";
import WritingLayout from "./WritingLayout";
import useUnsavedChanges from "./useUnsavedChanges";
import { sourceTaskChapter, useSourceLocationStore } from "../../state/sourceLocation";

export default function WorkspaceShell() {
  useUnsavedChanges();
  const { bindings, currentId, ready, error, notice, init, selectWorkspace, refreshAll, dismissError, dismissNotice } = useWorkspaceStore();
  const page = useWorkspacePage();
  const [directory, setDirectory] = useState(false);
  const returnPage = useRef<WorkspacePage | null>(null);
  const library = page?.kind === "library" ? page : null;
  const contextId = library?.workspaceContextId && bindings.some(item => item.id === library.workspaceContextId && item.exists) ? library.workspaceContextId : null;
  const initModels = useModelsStore(state => state.init);
  useEffect(() => {
    const initial = parseHash(window.location.hash);
    void init(initial && initial.kind !== "library" ? initial.workspaceId : initial?.workspaceContextId);
    void initModels();
  }, [init, initModels]);
  useEffect(() => {
    if (!ready) return;
    if (library) {
      if (library.workspaceContextId && !contextId) navigate({ ...library, workspaceContextId: undefined }, true);
      else if (contextId && contextId !== currentId) void selectWorkspace(contextId);
      return;
    }
    const target = page && page.kind !== "library" ? page.workspaceId : undefined;
    if (target && target !== currentId && bindings.some(item => item.id === target && item.exists)) {
      void selectWorkspace(target); return;
    }
    if (currentId && (!page || target !== currentId)) navigate({ kind: "writing", workspaceId: currentId }, true);
    else if (page?.kind === "writing") navigate(page, true);
  }, [page, bindings, currentId, ready, library, contextId, selectWorkspace]);
  useEffect(() => {
    if (page && page.kind !== "library" && page.workspaceId === currentId) {
      returnPage.current = page;
      usePagePositions.getState().remember(page);
    }
  }, [page, currentId]);
  useEffect(() => { if (currentId && !library) void useModuleLibraryStore.getState().trackAssembly(currentId); }, [currentId, Boolean(library)]);
  // 旧灵感路由只是打开同一个面板：`?entry=` 定位到具体记录，页面本身仍是写作页。
  const surfaceOpen = useIdeasSurfaceStore(state => state.open);
  const showIdeas = useIdeasSurfaceStore(state => state.show);
  useEffect(() => {
    if (!ready || !currentId || page?.kind !== "writing" || page.workspaceId !== currentId) return;
    if (!page.sourceId) { useSourceLocationStore.getState().clear(); return; }
    const taskChapter = page.taskChapter ?? sourceTaskChapter(currentId) ?? useWorkspaceStore.getState().file?.chapter_number ?? 0;
    void useSourceLocationStore.getState().load(currentId, page.sourceId, page.asOfChapter ?? null, taskChapter);
  }, [ready, currentId, page]);
  useEffect(() => {
    const source = useSourceLocationStore.getState();
    if (source.workspaceId && source.workspaceId !== currentId) source.clear();
  }, [currentId]);
  useEffect(() => {
    if (page?.kind !== "ideas" || !ready) return;
    showIdeas({ expanded: true, entryId: page.ideaId ?? null });
  }, [page, ready, showIdeas]);
  // 面板被收起后，旧灵感路由回到写作页；只处理「曾经打开过」的情况，
  // 避免首帧还没载入完成就把链接改掉。
  const surfaceWasOpen = useRef(false);
  useEffect(() => {
    if (surfaceOpen) {
      surfaceWasOpen.current = true;
      return;
    }
    if (!surfaceWasOpen.current) return;
    surfaceWasOpen.current = false;
    if (page?.kind === "ideas" && currentId) {
      navigate({ kind: "writing", workspaceId: currentId }, true);
    }
  }, [surfaceOpen, page, currentId]);
  useEffect(() => {
    const focus = () => {
      if (useWorkspaceStore.getState().currentId) { void refreshAll(); void useConversationsStore.getState().refreshList(); }
      void useModelsStore.getState().refresh();
    };
    window.addEventListener("focus", focus); return () => window.removeEventListener("focus", focus);
  }, [refreshAll]);
  function select(id: string) {
    if (library) navigate({ ...library, workspaceContextId: id });
    else {
      void selectWorkspace(id);
      const kind = page && page.kind !== "library" ? page.kind : "writing";
      navigate(usePagePositions.getState().pages[id]?.[kind] ?? { kind: "writing", workspaceId: id });
    }
  }
  function leaveLibrary() {
    const target = returnPage.current ?? (currentId ? { kind: "writing" as const, workspaceId: currentId } : null);
    if (target) navigate(target);
    else { useModuleLibraryStore.getState().closeDrawer(); window.location.hash = ""; }
  }
  const writing = !page || page.kind === "writing" || page.kind === "ideas";
  return <div className="app-shell">
    <AppHeader page={page} onOpen={() => setDirectory(true)} onSelect={select} />
    {error && <StatusMessage tone="error" action={<Button size="compact" variant="quiet" onClick={dismissError}>知道了</Button>}>{error}</StatusMessage>}
    {notice && <StatusMessage tone="warning" action={<Button size="compact" variant="quiet" onClick={dismissNotice}>知道了</Button>}>{notice}</StatusMessage>}
    <main className="app-content">
      <div className="workspace-page" hidden={!writing}><WritingLayout onOpen={() => setDirectory(true)} /></div>
      <div className="workspace-page" hidden={!library}><LibraryRoute page={library} contextId={contextId} onReturn={leaveLibrary} /></div>
    </main>
    <IdeasSurface />
    <DirectoryDialog open={directory} onClose={() => setDirectory(false)} />
    <ModelSettingsDialog /><ActivityPanel />
  </div>;
}
