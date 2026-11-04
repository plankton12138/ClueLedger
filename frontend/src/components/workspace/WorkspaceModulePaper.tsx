/** 作品模块只读稿纸（v1.2）。模块内容的修改从对话发起。 */

import { useEffect, useLayoutEffect, useRef } from "react";

import type { AssemblyBinding } from "../../api/moduleLibrary";
import { bindingLabel } from "../../state/moduleLibrary";
import { documentKey } from "../../state/moduleContent";
import { useWorkspaceModulesStore } from "../../state/workspaceModules";
import ModuleContent from "../library/ModuleContent";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";
import SelectionToChat from "./SelectionToChat";

export default function WorkspaceModulePaper({
  workspaceId: _workspaceId,
  binding,
}: {
  workspaceId: string;
  binding: AssemblyBinding;
}) {
  const store = useWorkspaceModulesStore();
  const key = documentKey(binding.module);
  const document = store.documents[key];
  const scroll = useRef<HTMLDivElement>(null);

  useEffect(() => { void store.load(binding); }, [binding, key, store.load]);
  useLayoutEffect(() => {
    const anchor = store.selected?.anchor;
    if (!document || !scroll.current) return;
    if (!anchor) {
      scroll.current.scrollTop = 0;
      return;
    }
    const target = scroll.current.querySelector<HTMLElement>(`#${CSS.escape(anchor)}`);
    if (target) {
      scroll.current.scrollTop +=
        target.getBoundingClientRect().top - scroll.current.getBoundingClientRect().top - 16;
    }
  }, [document, key, store.selected]);

  return <article className="workspace-module-paper" aria-label="模块内容">
    <header className="workspace-module-toolbar"><h2>{bindingLabel(binding)}</h2></header>
    {store.errors[key] && <StatusMessage
      tone="error"
      action={<Button size="compact" onClick={() => void store.load(binding)}>重试</Button>}
    >{store.errors[key]}</StatusMessage>}
    <div className="workspace-module-scroll" ref={scroll}>
      {store.loading[key] && !document
        ? <StatusMessage>读取模块内容…</StatusMessage>
        : document
          ? <SelectionToChat source={{
              title: bindingLabel(binding),
              source: `module:${binding.module.library_id}/${binding.module.module_id} (binding:${binding.id})`,
              version: binding.module.revision_id,
            }}><ModuleContent document={document} /></SelectionToChat>
          : null}
    </div>
    <span className="sr-only">模块内容只读；需要调整时请在对话中说明。</span>
  </article>;
}
