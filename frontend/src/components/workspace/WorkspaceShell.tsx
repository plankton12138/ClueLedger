/** 应用级导航与领域页面装配。页面切换只改变显示，不重建任务或内容草稿。 */
import { useEffect, useRef, useState } from "react";
import { useConversationsStore } from "../../state/conversations";
import { useModuleLibraryStore } from "../../state/moduleLibrary";
import { useModelsStore } from "../../state/models";
import { navigate, parseHash, useWorkspacePage, type WorkspacePage } from "../../state/navigation";
import { usePagePositions } from "../../state/pagePositions";
import { useWorkspaceStore } from "../../state/workspace";
import StoryMaterialsPage from "../materials/StoryMaterialsPage";
import IdeasPage from "../ideas/IdeasPage";
import LibraryRoute from "../library/LibraryRoute";
import ModelSettingsDialog from "../models/ModelSettingsDialog";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";
import ActivityPanel from "./ActivityPanel";
import DirectoryDialog from "./DirectoryDialog";
import AppHeader from "./AppHeader";
import WritingLayout from "./WritingLayout";
import useUnsavedChanges from "./useUnsavedChanges";

export default function WorkspaceShell() {
  useUnsavedChanges();
  const { bindings, currentId, ready, error, notice, init, selectWorkspace, refreshAll, openPath, openDrawer, dismissError, dismissNotice } = useWorkspaceStore();
  const page = useWorkspacePage();
  const positions = usePagePositions(state => currentId ? state.pages[currentId] : undefined);
  const [directory, setDirectory] = useState(false);
  const [visited, setVisited] = useState<Record<string, boolean>>({});
  const returnPage = useRef<WorkspacePage | null>(null);
  const library = page?.kind === "library" ? page : null;
  const ideas = page?.kind === "ideas" ? page : null;
  const materials = page?.kind === "materials" ? page : null;
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
  }, [page, bindings, currentId, ready, library, contextId, selectWorkspace]);
  useEffect(() => {
    if (page && page.kind !== "library" && page.workspaceId === currentId) {
      returnPage.current = page;
      usePagePositions.getState().remember(page);
      setVisited(previous => previous[`${currentId}:${page.kind}`] ? previous : { ...previous, [`${currentId}:${page.kind}`]: true });
    }
  }, [page, currentId]);
  useEffect(() => { if (currentId && !library) void useModuleLibraryStore.getState().trackAssembly(currentId); }, [currentId, Boolean(library)]);
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
  const writing = !page || page.kind === "writing";
  const rememberedIdeas = ideas ?? (positions?.ideas?.kind === "ideas" ? positions.ideas : null);
  const rememberedMaterials = materials ?? (positions?.materials?.kind === "materials" ? positions.materials : null);
  return <div className="app-shell">
    <AppHeader page={page} onOpen={() => setDirectory(true)} onSelect={select} />
    {error && <StatusMessage tone="error" action={<Button size="compact" variant="quiet" onClick={dismissError}>知道了</Button>}>{error}</StatusMessage>}
    {notice && <StatusMessage tone="warning" action={<Button size="compact" variant="quiet" onClick={dismissNotice}>知道了</Button>}>{notice}</StatusMessage>}
    <main className="app-content">
      <div className="workspace-page" hidden={!writing}><WritingLayout onOpen={() => setDirectory(true)} /></div>
      {currentId && (ideas || visited[`${currentId}:ideas`]) && <div key={`${currentId}:ideas`} className="workspace-page" hidden={!ideas}>
        <IdeasPage workspaceId={currentId} ideaId={rememberedIdeas?.ideaId} onNavigate={navigate} onOpenSource={path => { void openPath(path).then(() => navigate({ kind: "writing", workspaceId: currentId })); }} onTakeToChat={(id, revision, title) => {
          useConversationsStore.getState().addIdeaReference(id, revision, title); navigate({ kind: "writing", workspaceId: currentId });
        }} />
      </div>}
      {currentId && rememberedMaterials && <div key={`${currentId}:materials`} className="workspace-page" hidden={!materials}>
        <StoryMaterialsPage workspaceId={currentId} section={rememberedMaterials.section} chapter={rememberedMaterials.chapter} order={rememberedMaterials.order} entryId={rememberedMaterials.entryId} onNavigate={navigate}
          onOpenSource={path => { void openPath(path).then(() => navigate({ kind: "writing", workspaceId: currentId })); }}
          onTakeToChat={path => { useConversationsStore.getState().addReference(path); void openPath(path).then(() => navigate({ kind: "writing", workspaceId: currentId })); }}
          onOpenActivity={() => void openDrawer()} />
      </div>}
      <div className="workspace-page" hidden={!library}><LibraryRoute page={library} contextId={contextId} onReturn={leaveLibrary} /></div>
    </main>
    <DirectoryDialog open={directory} onClose={() => setDirectory(false)} />
    <ModelSettingsDialog /><ActivityPanel />
  </div>;
}
