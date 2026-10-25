import { useState } from "react";
import type { ModuleInfo } from "../../api/moduleLibrary";
import { useModuleLibraryStore } from "../../state/moduleLibrary";
import { Button } from "../ui/Button";
import { AppDialog } from "../ui/AppDialog";
import { Field } from "../ui/Field";
import { navigate } from "../../state/navigation";

export default function ModuleActions({ info }: { info: ModuleInfo | null | undefined }) {
  const store = useModuleLibraryStore();
  const [fork, setFork] = useState(false), [name, setName] = useState("");
  const [target, setTarget] = useState<ModuleInfo | null>(null);
  if (!info) return null;
  return <div className="module-actions">
    <AppDialog title="另存变体" open={fork} onOpenChange={setFork} trigger={<Button variant="quiet" disabled={store.busy}>另存变体</Button>}>
      <div className="ui-action-list"><Field label="变体名称"><input aria-label="变体名称" value={name} onChange={event => setName(event.target.value)} /></Field>
        <Button variant="primary" busy={store.busy} disabled={!name.trim()} onClick={() => {
          void store.fork(name).then(ok => { if (ok) {
            setFork(false); setName("");
            const current = useModuleLibraryStore.getState();
            if (current.selectedModuleId) navigate({ kind: "library", moduleId: current.selectedModuleId, revisionId: current.selectedRevisionId ?? undefined, workspaceContextId: current.workspaceId ?? undefined });
          } });
        }}>另存</Button>{store.error && <p role="alert" className="ui-field-error">{store.error}</p>}
      </div>
    </AppDialog>
    {info.deleted ? <Button variant="quiet" busy={store.busy} onClick={() => void store.restoreSelected()}>恢复模块</Button>
      : <Button variant="danger" disabled={store.busy} onClick={() => setTarget(info)}>删除模块</Button>}
    <AppDialog title="删除模块" open={Boolean(target)} onOpenChange={open => { if (!open && !store.busy) setTarget(null); }}>
      <div className="ui-action-list"><p>删除“{target?.name}”？已装入的作品仍可读取固定版本，模块可恢复。</p>
        <Button variant="danger" busy={store.busy} disabled={target?.module_id !== store.selectedModuleId} onClick={() => {
          if (target?.module_id !== store.selectedModuleId) return;
          void store.trashSelected().then(() => { if (!useModuleLibraryStore.getState().error) setTarget(null); });
        }}>确认删除</Button>{store.error && <p role="alert" className="ui-field-error">{store.error}</p>}
      </div>
    </AppDialog>
  </div>;
}
