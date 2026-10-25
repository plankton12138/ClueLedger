import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, MoreHorizontal, Plus } from "lucide-react";
import { putAssembly, requestKey, type AssemblyBinding, type AssemblyView } from "../../api/moduleLibrary";
import { errorMessage } from "../../api/errors";
import { bindingLabel, bindingProblems, useModuleLibraryStore } from "../../state/moduleLibrary";
import { documentKey, moduleOutline } from "../../state/moduleContent";
import { navigate } from "../../state/navigation";
import { useWorkspaceModulesStore, workspaceDraftKey, workspaceModuleDirty } from "../../state/workspaceModules";
import { Button } from "../ui/Button";
import { AppDialog } from "../ui/AppDialog";

function MountedModule({ workspaceId,binding,index,count,onChoose,onRemove }: { workspaceId: string; binding: AssemblyBinding; index: number; count: number; onChoose?: () => void; onRemove: () => void }) {
  const library = useModuleLibraryStore();
  const modules = useWorkspaceModulesStore();
  const [expanded,setExpanded] = useState(index===0), [menu,setMenu] = useState(false);
  const key = documentKey(binding.module), document = modules.documents[key];
  const active = modules.selected?.workspaceId===workspaceId && modules.selected.bindingId===binding.id;
  const label = bindingLabel(binding);
  const dirty = workspaceModuleDirty(modules.drafts[workspaceDraftKey(workspaceId,binding)]);
  useEffect(() => { if (active) setExpanded(true); }, [active]);
  useEffect(() => { if (expanded || active || menu) void modules.load(binding); }, [expanded,active,menu,key,modules.load]);
  function select(anchor?: string) { modules.select(workspaceId,binding.id,anchor); navigate({ kind:"writing",workspaceId }); onChoose?.(); }
  const outline = document ? moduleOutline(document) : [];
  return <section className="mounted-module">
    <div className="mounted-module-row" data-active={active && !modules.selected?.anchor}>
      <button type="button" className="mounted-module-disclosure" aria-label={`${expanded ? "收起" : "展开"}${label}`} aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button>
      <button type="button" className="mounted-module-name" aria-current={active && !modules.selected?.anchor ? "page" : undefined} onClick={() => select()}>{label}</button>
      <AppDialog title={label} open={menu} onOpenChange={setMenu} trigger={<Button size="compact" variant="quiet" aria-label={`管理${label}`}><MoreHorizontal size={15} /></Button>}>
        <div className="ui-action-list">
          <Button disabled={!document || Boolean(modules.busy)} onClick={() => { setMenu(false); void modules.saveToLibrary(workspaceId,binding); }}>另存到模块库</Button>
          {index>0 && <Button disabled={library.busy} onClick={() => { setMenu(false); void library.reorder(binding.id,-1); }}>上移</Button>}
          {index<count-1 && <Button disabled={library.busy} onClick={() => { setMenu(false); void library.reorder(binding.id,1); }}>下移</Button>}
          <Button variant="danger" disabled={library.busy || Boolean(modules.busy) || dirty} disabledReason={dirty ? "先保存或取消模块草稿" : undefined} onClick={() => { setMenu(false); onRemove(); }}>从作品移除</Button>
        </div>
      </AppDialog>
    </div>
    {expanded && <div className="mounted-module-outline">
      {modules.loading[key] && !document && <span className="ui-field-hint">读取内容…</span>}
      {outline.map(item => <button type="button" key={item.id} className="mounted-module-heading" data-active={active && modules.selected?.anchor===item.id} aria-current={active && modules.selected?.anchor===item.id ? "location" : undefined} onClick={() => select(item.id)}>{item.title}</button>)}
      {modules.errors[key] && <button type="button" className="mounted-module-heading" onClick={() => void modules.load(binding)}>重新读取内容</button>}
    </div>}
    {bindingProblems(binding) && <p className="ui-field-hint">{bindingProblems(binding)}</p>}
  </section>;
}

export default function WorkspaceModules({ workspaceId,onChoose }: { workspaceId: string; onChoose?: () => void }) {
  const library = useModuleLibraryStore();
  const modules = useWorkspaceModulesStore();
  const assembly = library.workspaceId===workspaceId ? library.assembly : null;
  const [removed,setRemoved] = useState<{ binding: AssemblyBinding; index: number; links: AssemblyView["links"] } | null>(null);
  const [error,setError] = useState<string | null>(null);
  const [restoring,setRestoring] = useState(false);
  useEffect(() => { setRemoved(null); setError(null); }, [workspaceId]);
  async function remove(binding: AssemblyBinding,index: number) {
    if (!assembly) return;
    const links = assembly.links.filter(([a,b]) => a===binding.id || b===binding.id);
    await library.unbind(binding.id);
    const current = useModuleLibraryStore.getState();
    if (current.workspaceId!==workspaceId) return;
    if (!current.error && !current.assembly?.bindings.some(item => item.id===binding.id)) {
      setRemoved({ binding,index,links });
      if (useWorkspaceModulesStore.getState().selected?.bindingId===binding.id) modules.close();
    }
  }
  async function undo() {
    const current = useModuleLibraryStore.getState();
    if (restoring || !removed || current.workspaceId!==workspaceId || !current.assembly) return;
    if (current.assembly.bindings.some(item => item.module.module_id===removed.binding.module.module_id)) { setRemoved(null); return; }
    const rows = [...current.assembly.bindings]; rows.splice(Math.min(removed.index,rows.length),0,removed.binding);
    const ids = new Set(rows.map(item=>item.id));
    setRestoring(true); setError(null);
    try {
      const restored = await putAssembly(workspaceId,{ bindings:rows.map(({ id,label,module })=>({ id,label,module })), links:[...current.assembly.links,...removed.links.filter(([a,b])=>ids.has(a)&&ids.has(b))], expected_digest:current.assembly.digest,request_key:requestKey("undo-remove") });
      const latest = useModuleLibraryStore.getState();
      if (latest.workspaceId===workspaceId && latest.session===current.session) { useModuleLibraryStore.setState({ assembly:restored,assemblyEpoch:latest.assemblyEpoch+1 }); setRemoved(null); }
    } catch(reason) { if(useModuleLibraryStore.getState().workspaceId===workspaceId)setError(errorMessage(reason,"恢复模块")); }
    finally { setRestoring(false); }
  }
  return <section className="workspace-modules" aria-label="作品模块">
    <div className="workspace-modules-heading"><h2>模块</h2><Button variant="quiet" size="compact" aria-label="添加模块" onClick={() => navigate({ kind:"library",workspaceContextId:workspaceId })}><Plus size={15} /></Button></div>
    {assembly?.bindings.map((binding,index)=><MountedModule key={`${workspaceId}:${binding.id}`} workspaceId={workspaceId} binding={binding} index={index} count={assembly.bindings.length} onChoose={onChoose} onRemove={() => void remove(binding,index)} />)}
    {assembly && !assembly.bindings.length && <p className="workspace-module-empty">还没有模块</p>}
    {!assembly && <p className="workspace-module-empty">读取模块…</p>}
    {(error || (!library.open && library.workspaceId===workspaceId && library.error)) && <p role="alert" className="ui-field-error">{error ?? library.error}<Button size="compact" variant="quiet" onClick={() => { setError(null); library.clearNotice(); void library.loadAssembly(); }}>重新读取</Button></p>}
    {modules.message?.workspaceId===workspaceId && (library.open || modules.selected?.workspaceId!==workspaceId) && <p role={modules.message.error?"alert":"status"} className="ui-field-hint">{modules.message.text}</p>}
    {removed && <div className="workspace-module-undo" role="status">已移除<Button size="compact" variant="quiet" busy={restoring} onClick={() => void undo()}>撤销</Button></div>}
  </section>;
}
