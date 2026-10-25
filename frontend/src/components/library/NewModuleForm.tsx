import { useEffect, useState } from "react";
import { requestKey, type ModuleKind } from "../../api/moduleLibrary";
import { useModuleEditorStore } from "../../state/moduleEditor";
import { useModuleLibraryStore } from "../../state/moduleLibrary";
import { contentName } from "../../state/moduleContent";
import { useWorkspaceModulesStore } from "../../state/workspaceModules";
import { navigate } from "../../state/navigation";
import { Button } from "../ui/Button";

export default function NewModuleForm({onDone}:{onDone:()=>void}) {
  const [id]=useState(()=>useModuleEditorStore.getState().activeNewDraftId??requestKey("new-module"));
  useEffect(()=>{useModuleEditorStore.setState({activeNewDraftId:id});},[id]);
  const editor=useModuleEditorStore(),store=useModuleLibraryStore();
  const form=editor.newDrafts[id]??{kind:"reference" as ModuleKind,name:"",title:"",body:""};
  function patch(value:Partial<typeof form>){useModuleEditorStore.setState(state=>({newDrafts:{...state.newDrafts,[id]:{...form,...value}}}));}
  function clear(){useModuleEditorStore.setState(state=>{const next={...state.newDrafts};delete next[id];return{newDrafts:next,activeNewDraftId:state.activeNewDraftId===id?null:state.activeNewDraftId};});}
  async function save(){
    const session=store.session,workspaceId=store.workspaceId;
    const input={...form,kind:"reference" as ModuleKind,name:contentName(form.name,form.body),title:contentName(form.name,form.body),draftId:id};
    if(!await store.create(input))return;
    const live=useModuleLibraryStore.getState();
    if(live.session!==session||live.workspaceId!==workspaceId)return;
    const result=live.creations[JSON.stringify(input)]?.result;
    if(!result)return;
    if(workspaceId){
      const found=live.assembly?.bindings.find(item=>item.module.module_id===result.module.module_id);
      if(!found)await live.bind(result.module.module_id,result.revision.revision_id);
      const latest=useModuleLibraryStore.getState();
      if(latest.session!==session||latest.error)return;
      const binding=latest.assembly?.bindings.find(item=>item.module.module_id===result.module.module_id);
      if(!binding)return;
      clear();useWorkspaceModulesStore.getState().select(workspaceId,binding.id);navigate({kind:"writing",workspaceId});
    }else{clear();onDone();}
  }
  return <div className="new-module-form module-document-editor"><input aria-label="模块名称" maxLength={80} placeholder="未命名模块" value={form.name} disabled={store.busy} onChange={event=>patch({name:event.target.value})}/>
    <textarea aria-label="模块正文" rows={16} placeholder="直接写下内容…" value={form.body} disabled={store.busy} onChange={event=>patch({body:event.target.value})}/>
    <div className="module-edit-actions"><Button variant="primary" busy={store.busy} onClick={()=>void save()}>{store.workspaceId?"保存并添加":"保存"}</Button><Button disabled={store.busy} variant="quiet" onClick={()=>{clear();onDone();}}>取消</Button></div>
  </div>;
}
