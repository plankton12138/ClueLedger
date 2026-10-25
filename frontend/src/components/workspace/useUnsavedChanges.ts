import { useEffect } from "react";
import { useModuleEditorStore, draftDirty } from "../../state/moduleEditor";
import { draftDirty as ideaDraftDirty, useIdeasStore } from "../../state/ideas";

/** 不持久化原文；页面卸载前提醒本次会话中尚未保存的输入。 */
export function hasUnsavedChanges(): boolean {
  const modules = useModuleEditorStore.getState();
  const ideas = useIdeasStore.getState();
  const ideaDirty = Object.values(ideas.scopes).some(
    scope =>
      scope.capture.body.length > 0 ||
      Object.values(scope.editing).some(draft => ideaDraftDirty(draft)),
  );
  return (
    Object.values(modules.drafts).some(draftDirty) ||
    Object.values(modules.newDrafts).some(draft => Boolean(draft.name || draft.title || draft.body)) ||
    ideaDirty
  );
}
export default function useUnsavedChanges() {
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!hasUnsavedChanges()) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, []);
}
