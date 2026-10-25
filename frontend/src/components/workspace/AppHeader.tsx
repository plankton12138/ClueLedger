import { useState } from "react";
import { pageHash, type AppPage, type WorkspacePage } from "../../state/navigation";
import { useWorkspaceStore } from "../../state/workspace";
import { useModelsStore } from "../../state/models";
import { usePagePositions } from "../../state/pagePositions";
import { Button } from "../ui/Button";
import { AppDialog } from "../ui/AppDialog";
import FontSizeControl from "./FontSizeControl";

export function WorkspaceNavigation({ page, workspaceId }: { page: AppPage | null; workspaceId: string | null }) {
  const positions = usePagePositions(state => workspaceId ? state.pages[workspaceId] : undefined);
  const defaults: WorkspacePage[] = workspaceId ? [
    { kind: "writing", workspaceId }, { kind: "ideas", workspaceId },
    { kind: "materials", workspaceId, section: "story", chapter: null, order: "chapter" },
  ] : [];
  return <nav className="workspace-navigation" aria-label="作品页面">{["写作", "灵感", "故事资料"].map((label, index) => {
    const target = defaults[index];
    return target ? <a key={label} href={pageHash(positions?.[target.kind] ?? target)} aria-current={page?.kind === target.kind ? "page" : undefined}>{label}</a>
      : <span key={label} aria-disabled="true">{label}</span>;
  })}</nav>;
}

export function WorkspaceMenu({ onOpen }: { onOpen: () => void }) {
  const [open, setOpen] = useState(false);
  const { currentId, bindings, refreshAll, pruneMissing } = useWorkspaceStore();
  const stale = bindings.filter(item => !item.exists).length;
  return <AppDialog open={open} onOpenChange={setOpen} title="作品" trigger={<Button size="compact" data-dialog-return="workspace">作品</Button>}>
    <div className="ui-action-list">
      <Button onClick={() => { setOpen(false); onOpen(); }}>打开 / 新建</Button>
      <Button disabled={!currentId} disabledReason="先打开作品目录" onClick={() => { setOpen(false); void refreshAll(); }}>重新读取文件</Button>
      {stale > 0 && <Button variant="danger" onClick={() => void pruneMissing()}>清理失效（{stale}）</Button>}
      {!currentId && <p className="ui-field-hint">先打开作品目录。</p>}
    </div>
  </AppDialog>;
}

export default function AppHeader({ page, onOpen, onSelect }: { page: AppPage | null; onOpen: () => void; onSelect: (id: string) => void }) {
  const { bindings, currentId } = useWorkspaceStore();
  const [settings, setSettings] = useState(false);
  const openModels = useModelsStore(state => state.openSettings);
  return <header className="app-header">
    <div className="workspace-picker"><span className="app-name">ClueLedger</span>
      <select className="ui-input" aria-label="选择作品目录" value={currentId ?? ""} onChange={event => event.target.value && onSelect(event.target.value)}>
        <option value="">（未打开目录）</option>{bindings.map(item => <option key={item.id} value={item.id}>{item.name}{item.exists ? "" : "（目录已不在）"}</option>)}
      </select><WorkspaceMenu onOpen={onOpen} />
    </div>
    <WorkspaceNavigation page={page} workspaceId={currentId} />
    <div className="app-tools"><a className="ui-nav-link" href={pageHash({ kind: "library", workspaceContextId: currentId ?? undefined })} aria-current={page?.kind === "library" ? "page" : undefined}>模块库</a>
      <AppDialog title="设置" open={settings} onOpenChange={setSettings} trigger={<Button size="compact" data-dialog-return="settings">设置</Button>}>
        <div className="ui-action-list"><Button onClick={() => { setSettings(false); openModels(); }}>模型设置</Button><FontSizeControl /></div>
      </AppDialog>
    </div>
  </header>;
}
