import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { AssemblyBinding } from "../../api/moduleLibrary";
import { bindingLabel, useModuleLibraryStore } from "../../state/moduleLibrary";
import { contentName, documentKey } from "../../state/moduleContent";
import { workspaceDraftKey, workspaceModuleDirty, useWorkspaceModulesStore } from "../../state/workspaceModules";
import ModuleContent from "../library/ModuleContent";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";
import SelectionToChat from "./SelectionToChat";

export default function WorkspaceModulePaper({ workspaceId,binding }: { workspaceId: string; binding: AssemblyBinding }) {
  const store = useWorkspaceModulesStore();
  const key = documentKey(binding.module), draftKey = workspaceDraftKey(workspaceId,binding);
  const document = store.documents[key], draft = store.drafts[draftKey];
  const [editing,setEditing] = useState(false);
  const scroll = useRef<HTMLDivElement>(null);
  const busy = Boolean(store.busy);
  useEffect(() => { void store.load(binding); }, [key,store.load]);
  useEffect(() => { setEditing(false); }, [draftKey,key]);
  useLayoutEffect(() => {
    const anchor = store.selected?.anchor;
    if (!editing && document && scroll.current) {
      if (!anchor) { scroll.current.scrollTop=0; return; }
      const target = scroll.current?.querySelector<HTMLElement>(`#${CSS.escape(anchor)}`);
      if (target) scroll.current.scrollTop += target.getBoundingClientRect().top-scroll.current.getBoundingClientRect().top-16;
    }
  }, [store.selected,key,Boolean(document),editing]);
  const message = store.message?.workspaceId === workspaceId ? store.message : null;
  return <article className="workspace-module-paper" aria-label="模块内容">
    <header className="workspace-module-toolbar"><h2>{bindingLabel(binding)}</h2>
      <div className="module-edit-actions">{editing && draft ? <>
        <Button variant="primary" busy={busy} disabled={!workspaceModuleDirty(draft)} onClick={() => {
          store.edit(draftKey,{ name: contentName(draft.name,draft.entries[0]?.body ?? "") });
          void store.save(workspaceId,binding).then(ok => { if (ok) setEditing(false); });
        }}>保存</Button>
        <Button variant="quiet" disabled={busy} onClick={() => { store.discard(draftKey); setEditing(false); }}>取消</Button>
      </> : <Button disabled={!document || busy} onClick={() => { store.begin(workspaceId,binding); setEditing(true); }}>编辑</Button>}</div>
    </header>
    {message && <StatusMessage tone={message.error ? "error" : "info"} action={message.error ? <div className="module-edit-actions">
      <Button size="compact" variant="quiet" onClick={() => void useModuleLibraryStore.getState().loadAssembly()}>重新读取</Button>
      {draft && <Button size="compact" disabled={busy} onClick={() => void store.saveToLibrary(workspaceId,binding,draft)}>另存草稿</Button>}
    </div> : <Button size="compact" variant="quiet" onClick={store.clearMessage}>知道了</Button>}>{message.text}</StatusMessage>}
    {!editing && workspaceModuleDirty(draft) && <StatusMessage tone="warning" action={<Button size="compact" onClick={() => setEditing(true)}>继续编辑</Button>}>有未保存修改。</StatusMessage>}
    {store.errors[key] && <StatusMessage tone="error" action={<Button size="compact" onClick={() => void store.load(binding)}>重试</Button>}>{store.errors[key]}</StatusMessage>}
    <div className="workspace-module-scroll" ref={scroll}>
      {store.loading[key] && !document ? <StatusMessage>读取模块内容…</StatusMessage> : editing && draft ? <div className="module-document-editor">
        <input aria-label="模块名称" placeholder="未命名模块" maxLength={80} disabled={busy} value={draft.name} onChange={event => store.edit(draftKey,{ name: event.target.value })} />
        {draft.entries.map((entry,index) => <div className="module-document-section" key={entry.entry_id}>
          {draft.entries.length > 1 && <h3>{entry.title}</h3>}
          <textarea aria-label={draft.entries.length > 1 ? `模块正文 ${entry.title}` : "模块正文"} rows={14} disabled={busy} value={entry.body}
            onChange={event => store.edit(draftKey,{ entries: draft.entries.map((item,i) => i===index ? { ...item,body:event.target.value } : item) })} />
        </div>)}
      </div> : document ? <SelectionToChat source={{ title: bindingLabel(binding),
        source: `module:${binding.module.library_id}/${binding.module.module_id} (binding:${binding.id})`,
        version: binding.module.revision_id }}><ModuleContent document={document} /></SelectionToChat> : null}
    </div>
  </article>;
}
