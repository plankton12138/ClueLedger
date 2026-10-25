import { useEffect, useRef } from "react";
import { useModuleLibraryStore } from "../../state/moduleLibrary";
import { navigate, parseHash, type LibraryPage } from "../../state/navigation";
import ModuleLibraryPage from "./ModuleLibraryPage";

export default function LibraryRoute({ page, contextId, onReturn }: { page: LibraryPage | null; contextId: string | null; onReturn: () => void }) {
  const revision = useModuleLibraryStore(state => state.selectedRevisionId);
  const detail = useModuleLibraryStore(state => state.detail);
  const syncing = useRef(false);
  useEffect(() => {
    let cancelled = false;
    if (!page) { useModuleLibraryStore.getState().closeDrawer(); return; }
    syncing.current = true;
    void (async () => {
      const store = useModuleLibraryStore.getState();
      if (!store.open || store.workspaceId !== contextId) await store.openDrawer(contextId);
      if (cancelled) return;
      const current = useModuleLibraryStore.getState();
      if (page.moduleId && (current.selectedModuleId !== page.moduleId || (page.revisionId && current.selectedRevisionId !== page.revisionId))) {
        await current.selectModule(page.moduleId, page.revisionId);
      }
      if (cancelled) return;
      syncing.current = false;
      const loaded = useModuleLibraryStore.getState();
      if (page.moduleId && loaded.detail && !page.revisionId) navigate({ ...page, workspaceContextId: contextId ?? undefined, revisionId: loaded.selectedRevisionId ?? undefined }, true);
    })();
    return () => { cancelled = true; };
  }, [page?.moduleId, page?.revisionId, page?.workspaceContextId, Boolean(page), contextId]);
  useEffect(() => {
    if (!page || syncing.current || !detail || detail.module_id !== page.moduleId || !revision || detail.revision_id !== revision) return;
    const location = parseHash(window.location.hash);
    if (location?.kind === "library" && location.moduleId === detail.module_id && location.revisionId !== revision) navigate({ ...location, revisionId: revision }, true);
  }, [revision, detail, page]);
  return <ModuleLibraryPage onClose={onReturn} />;
}
