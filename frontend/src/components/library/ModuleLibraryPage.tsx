/** 应用级模块库；浏览、原文编辑和作品装配分别由领域组件承担。 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useModuleLibraryStore } from "../../state/moduleLibrary";
import { useModuleEditorStore, draftDirty, moduleDraftKey, draftEntries, fieldProblem } from "../../state/moduleEditor";
import { navigate } from "../../state/navigation";
import { useLayoutStore } from "../../state/layout";
import { useWorkspaceStore } from "../../state/workspace";
import { Button } from "../ui/Button";
import { AppDialog } from "../ui/AppDialog";
import { StatusMessage, EmptyState } from "../ui/StatusMessage";
import LibraryBrowser from "./LibraryBrowser";
import ModulePreview from "./ModulePreview";
import ModuleEditor from "./ModuleEditor";
import ModuleActions from "./ModuleActions";
import RevisionPicker from "./RevisionPicker";
import AssemblyPanel from "./AssemblyPanel";
import NewModuleForm from "./NewModuleForm";
import { KIND_LABEL } from "./labels";

export default function ModuleLibraryPage({ onClose }: { onClose: () => void }) {
  const store = useModuleLibraryStore();
  const editor = useModuleEditorStore();
  const bindings = useWorkspaceStore(state => state.bindings);
  const fontScale = useLayoutStore(state => state.fontScale);
  const root = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1440);
  const [creating, setCreating] = useState(false), [editing, setEditing] = useState(false);
  const [showDetail, setShowDetail] = useState(false), [assemblyOpen, setAssemblyOpen] = useState(false), [apply, setApply] = useState(false);
  useLayoutEffect(() => {
    if (!root.current) return;
    const observer = new ResizeObserver(entries => { if (entries[0].contentRect.width) setWidth(entries[0].contentRect.width); });
    observer.observe(root.current); return () => observer.disconnect();
  }, []);
  useEffect(() => { setEditing(false); setApply(false); setCreating(false); setShowDetail(Boolean(store.selectedModuleId)); }, [store.selectedModuleId]);
  useEffect(() => { if (!store.open) { setEditing(false); setShowDetail(false); setAssemblyOpen(false); } }, [store.open]);
  const detail = store.detail;
  const info = store.modules.find(item => item.module_id === store.selectedModuleId) ?? store.selectedInfo;
  const key = detail ? moduleDraftKey(detail) : null;
  const draft = key ? editor.drafts[key] : undefined;
  const rows = draft?.rows ?? [];
  const dirty = draftDirty(draft);
  const preset = store.presets.find(item => item.kind === detail?.kind);
  const problem = rows.map(row => fieldProblem(row, preset)).find(Boolean);
  const atHead = Boolean(info && detail?.revision_id === info.head);
  const narrow = width < 920 * Math.max(1, fontScale / 100);
  const context = bindings.find(item => item.id === store.workspaceId);
  const bound = store.assembly?.bindings.find(item => item.module.module_id === store.selectedModuleId);
  function select(moduleId: string, revisionId?: string | null) {
    setEditing(false); setCreating(false); setShowDetail(true); setApply(false); setAssemblyOpen(false);
    navigate({ kind: "library", moduleId, revisionId: revisionId ?? undefined, workspaceContextId: store.workspaceId ?? undefined });
  }
  const pending = Object.values(store.publications).filter(command => command.workspaceId && command.published && !command.applied && command.workspaceId === store.workspaceId && command.moduleId === store.selectedModuleId);
  return <div className="library-page" role="region" aria-label="模块库" hidden={!store.open} ref={root}>
    <div className="library-frame">
      <header className="library-header"><h1>模块库</h1><span className="library-context">{context ? `用于作品：${context.name}` : "选择作品后可装入模块或派生作品。"}</span>
        <div className="library-header-actions">
          <Button size="compact" disabled={!context} disabledReason="先选择已登记作品" onClick={() => setAssemblyOpen(true)}>作品已装模块</Button>
          <Button size="compact" disabled={!context || store.busy} disabledReason="先选择已登记作品" onClick={() => void store.importLegacy()}>导入旧资料</Button>
          <Button size="compact" variant="quiet" onClick={onClose}>返回</Button>
        </div>
      </header>
      {(store.notice || store.error) && <StatusMessage tone={store.error ? "error" : "info"} action={<Button variant="quiet" size="compact" onClick={store.clearNotice}>知道了</Button>}>
        {store.notice && <p>{store.notice}</p>}{store.error && <p>{store.error}</p>}
      </StatusMessage>}
      <div className="library-body" data-narrow={narrow}>
        <div className="library-browser-slot" hidden={narrow && showDetail}><LibraryBrowser onSelect={id => select(id)} onCreate={() => { setCreating(true); setShowDetail(true); }} /></div>
        <section className="library-detail module" hidden={narrow && !showDetail} aria-label="模块详情">
          {narrow && showDetail && <div className="library-detail-back"><Button size="compact" onClick={() => { setShowDetail(false); setCreating(false); }}>返回列表</Button></div>}
          <div className="library-new-slot" hidden={!creating}><NewModuleForm onDone={() => {
            setCreating(false);
            const current = useModuleLibraryStore.getState();
            if (current.selectedModuleId) select(current.selectedModuleId, current.selectedRevisionId);
          }} /></div>
          {!creating && (store.detailLoading ? <StatusMessage>读取完整版本…</StatusMessage> : detail ? <>
            <div className="library-detail-toolbar"><div><h2 title={detail.name}>{detail.name}</h2><p>{KIND_LABEL[detail.kind]}</p></div>
              <RevisionPicker head={info?.head} onSelect={revisionId => store.selectedModuleId && select(store.selectedModuleId, revisionId)} />
            </div>
            {!editing && dirty && <StatusMessage tone="warning" action={<Button size="compact" onClick={() => setEditing(true)}>继续编辑</Button>}>此版本有未保存修改。</StatusMessage>}
            <div className="library-detail-content">
              {editing ? <ModuleEditor rows={rows} preset={preset} onChange={update => { if (key) editor.edit(key, update); }} />
                : <ModulePreview detail={detail} bodies={store.bodies} />}
            </div>
            <footer className="library-detail-footer">
              {editing ? <>
                {context && <label className="module-apply-option"><input type="checkbox" checked={apply} onChange={event => setApply(event.target.checked)} />同时用于“{context.name}”</label>}
                <div className="module-edit-actions"><Button variant="primary" busy={store.busy} disabled={!dirty || !atHead || Boolean(info?.deleted) || Boolean(problem)}
                  disabledReason={!dirty ? "尚未修改内容" : !atHead ? "历史版本不能覆盖最新版本，可另存变体" : problem ?? undefined}
                  onClick={() => void (apply ? store.saveAndUse(draftEntries(rows)) : store.saveToLibrary(draftEntries(rows)))}>保存新版本</Button>
                  <Button variant="quiet" disabled={store.busy} onClick={() => setEditing(false)}>返回预览</Button>
                  <Button variant="danger" disabled={!dirty || store.busy} onClick={() => { if (key) editor.discard(key); }}>放弃修改</Button>
                </div>
                {!dirty && <p className="ui-field-hint">没有未保存修改。</p>}
              </> : <div className="module-edit-actions"><Button disabled={!atHead || Boolean(info?.deleted)} disabledReason="历史版本或已删除模块只能预览或另存变体" onClick={() => { setEditing(true); setApply(false); }}>编辑</Button>
                {bound ? <Button disabled={store.busy || bound.module.revision_id === detail.revision_id} disabledReason="作品已使用此版本" onClick={() => void store.upgrade(bound.id, detail.revision_id)}>切换本作品版本</Button>
                  : <Button disabled={store.busy || !context || Boolean(info?.deleted)} disabledReason="先选择作品并确认模块未删除" onClick={() => void store.bind(detail.module_id, detail.revision_id)}>装入本作品</Button>}
              </div>}
              {pending.map(command => <Button key={command.id} busy={command.running} onClick={() => void store.retryApplication(command.id)}>重试应用已发布版本</Button>)}
              <ModuleActions info={info} />
            </footer>
          </> : <EmptyState>选择一份模块查看内容与版本。</EmptyState>)}
        </section>
      </div>
    </div>
    <AppDialog title="作品已装模块" open={assemblyOpen} onOpenChange={setAssemblyOpen}>
      <AssemblyPanel onSelect={select} />
    </AppDialog>
  </div>;
}
