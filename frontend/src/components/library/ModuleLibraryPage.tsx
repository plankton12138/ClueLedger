/** 模块库负责内容复用；作品内容在写作区就地编辑。 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { useLayoutStore } from "../../state/layout";
import { useModuleLibraryStore } from "../../state/moduleLibrary";
import { useModuleEditorStore, draftDirty, moduleDraftKey, draftEntries, fieldProblem } from "../../state/moduleEditor";
import { navigate, useWorkspacePage } from "../../state/navigation";
import { useWorkspaceModulesStore } from "../../state/workspaceModules";
import type { ModuleInfo } from "../../api/moduleLibrary";
import { Button } from "../ui/Button";
import { AppDialog } from "../ui/AppDialog";
import { EmptyState, StatusMessage } from "../ui/StatusMessage";
import WorkspaceToc from "../workspace/WorkspaceToc";
import LibraryBrowser from "./LibraryBrowser";
import ModulePreview from "./ModulePreview";
import ModuleEditor from "./ModuleEditor";
import ModuleActions from "./ModuleActions";
import RevisionPicker from "./RevisionPicker";
import AssemblyPanel from "./AssemblyPanel";
import NewModuleForm from "./NewModuleForm";

export default function ModuleLibraryPage({onClose}:{onClose:()=>void}) {
  const store=useModuleLibraryStore(),editor=useModuleEditorStore(),page=useWorkspacePage();
  const [creating,setCreating]=useState(false),[editing,setEditing]=useState(false),[assemblyOpen,setAssemblyOpen]=useState(false);
  const root = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const scale = useLayoutStore(state => state.fontScale);
  const narrow = width < 1180 * Math.max(1, scale / 100);
  useLayoutEffect(() => {
    if (!root.current) return;
    const observer = new ResizeObserver(entries => { if (entries[0].contentRect.width > 0) setWidth(entries[0].contentRect.width); });
    observer.observe(root.current); return () => observer.disconnect();
  }, []);
  const showDetail=creating||(page?.kind==="library"&&Boolean(page.moduleId));
  useEffect(()=>{setEditing(false);setCreating(false);},[store.selectedModuleId]);
  useEffect(()=>{if(!store.open){setEditing(false);setCreating(false);}},[store.open]);
  const detail=store.detail,info=store.modules.find(item=>item.module_id===store.selectedModuleId)??store.selectedInfo;
  const key=detail?moduleDraftKey(detail):null,draft=key?editor.drafts[key]:undefined,rows=draft?.rows??[];
  const dirty=draftDirty(draft),preset=store.presets.find(item=>item.kind===detail?.kind);
  const problem=rows.map(row=>fieldProblem(row,preset)).find(Boolean),atHead=Boolean(info&&detail?.revision_id===info.head);
  const bound=store.assembly?.bindings.find(item=>item.module.module_id===store.selectedModuleId);
  function select(moduleId:string,revisionId?:string|null){setCreating(false);setEditing(false);navigate({kind:"library",moduleId,revisionId:revisionId??undefined,workspaceContextId:store.workspaceId??undefined});}
  function browse(){setCreating(false);setEditing(false);navigate({kind:"library",workspaceContextId:store.workspaceId??undefined});}
  async function add(item:Pick<ModuleInfo,"module_id"|"head">){
    if(!item.head||!store.workspaceId)return;
    const workspaceId=store.workspaceId,session=store.session;
    await store.bind(item.module_id,item.head);
    const current=useModuleLibraryStore.getState();
    if(current.session!==session||current.workspaceId!==workspaceId||current.error)return;
    const binding=current.assembly?.bindings.find(row=>row.module.module_id===item.module_id);
    if(binding){useWorkspaceModulesStore.getState().select(workspaceId,binding.id);navigate({kind:"writing",workspaceId});}
  }
  return <div className="library-page module-library-page" ref={root} data-narrow={narrow} role="region" aria-label="模块库" hidden={!store.open}>
    {store.open&&store.workspaceId&&<aside className="library-workspace-sidebar module"><WorkspaceToc onChoose={()=>{if(store.workspaceId)navigate({kind:"writing",workspaceId:store.workspaceId});}}/></aside>}
    <div className="library-frame">
      <header className="library-header"><Button size="compact" onClick={onClose}><ArrowLeft size={16} aria-hidden="true" />返回写作</Button><h1>{creating?"新建模块":"模块库"}</h1><div className="library-header-actions">
        {!creating&&<Button variant="primary" size="compact" onClick={()=>setCreating(true)}>新建模块</Button>}
        <details className="library-more"><summary>更多</summary><div className="library-more-content">
          <Button size="compact" disabled={!store.workspaceId} onClick={()=>setAssemblyOpen(true)}>作品模块管理</Button>
          <Button size="compact" disabled={!store.workspaceId||store.busy} onClick={()=>void store.importLegacy()}>导入旧资料</Button>
          <label className="library-deleted"><input type="checkbox" checked={store.includeDeleted} onChange={event=>store.setIncludeDeleted(event.target.checked)}/>显示已删除</label>
        </div></details>
      </div></header>
      {(store.notice||store.error)&&<StatusMessage tone={store.error?"error":"info"} action={<Button size="compact" variant="quiet" onClick={store.clearNotice}>知道了</Button>}>{store.notice&&<p>{store.notice}</p>}{store.error&&<p>{store.error}</p>}</StatusMessage>}
      <div className="library-body" data-narrow={narrow}>
        <LibraryBrowser hidden={narrow && showDetail} onSelect={id=>select(id)} onAdd={item=>void add(item)}/>
        <section className="library-detail module" aria-label="模块详情" hidden={narrow && !showDetail}>
          {!showDetail ? <EmptyState>选择一份模块，预览内容。</EmptyState> : <>
          <div className="library-detail-back"><Button variant="quiet" size="compact" onClick={browse}>返回列表</Button></div>
          {creating?<div className="library-new-slot"><NewModuleForm onDone={()=>{setCreating(false);const live=useModuleLibraryStore.getState();if(live.selectedModuleId)select(live.selectedModuleId,live.selectedRevisionId);else browse();}}/></div>:store.detailLoading?<StatusMessage>读取完整内容…</StatusMessage>:detail?<>
            <div className="library-detail-toolbar"><div><h2>{detail.name}</h2></div>{!editing && <Button size="compact" disabled={!atHead||Boolean(info?.deleted)} onClick={()=>setEditing(true)}>编辑</Button>}<details className="library-more"><summary>更多</summary><div className="library-more-content">
              <RevisionPicker head={info?.head} onSelect={revision=>store.selectedModuleId&&select(store.selectedModuleId,revision)}/><ModuleActions info={info}/>
            </div></details></div>
            {!editing&&dirty&&<StatusMessage tone="warning" action={<Button size="compact" onClick={()=>setEditing(true)}>继续编辑</Button>}>有未保存修改。</StatusMessage>}
            <div className="library-detail-content">{editing?<ModuleEditor rows={rows} preset={preset} onChange={update=>{if(key)editor.edit(key,update);}}/>:<ModulePreview detail={detail} bodies={store.bodies}/>}</div>
            <footer className="library-detail-footer"><div className="module-edit-actions">{editing?<>
              <Button variant="quiet" disabled={store.busy} onClick={()=>{if(key)editor.discard(key);setEditing(false);}}>取消</Button>
              <Button variant="primary" busy={store.busy} disabled={!dirty||!atHead||Boolean(info?.deleted)||Boolean(problem)} disabledReason={problem??undefined} onClick={()=>void store.saveToLibrary(draftEntries(rows)).then(ok=>{if(ok)setEditing(false);})}>保存</Button>
            </>:<>
              {bound?<Button disabled>已添加</Button>:<Button variant="primary" disabled={store.busy||!store.workspaceId||Boolean(info?.deleted)} disabledReason={!store.workspaceId?"先打开一个作品":undefined} onClick={()=>void add({module_id:detail.module_id,head:detail.revision_id})}>添加到作品</Button>}
            </>}</div></footer>
          </>:null}
          </>}
        </section>
      </div>
    </div>
    <AppDialog title="作品模块管理" open={assemblyOpen} onOpenChange={setAssemblyOpen}><AssemblyPanel onSelect={id=>{setAssemblyOpen(false);select(id);}}/></AppDialog>
  </div>;
}
